"""Transactional composite-review extension of the existing V4 ReviewRepository.

The old single-candidate reviews table stays compatible. Application records use
separate versioned tables in the SAME contained SQLite store and reuse its connection
and clock lifecycle. Immutable snapshots and append-only events are enforced by SQL.
"""
import hashlib
import json
import uuid
from .review_store import ReviewRepository
from .notifications import notification_topic,supported_backends,telegram_configured

class Conflict(ValueError):
    pass


def encode(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def approved_execution_key(item, *, audio_preference=None,target_id=None,sonarr_episode=None):
    """Return the canonical identity for one immutable approved revision."""
    identity={"item_id":item["id"],"snapshot_digest":item["digest"],
        "approved_revision":item["revision"]}
    if audio_preference is not None:identity["audio_preference"]=audio_preference
    if target_id is not None or sonarr_episode is not None:
        if not isinstance(target_id,str) or not target_id or not isinstance(sonarr_episode,int) \
                or isinstance(sonarr_episode,bool) or sonarr_episode<1:
            raise ValueError("Execution scope requires a target and positive Sonarr episode")
        identity["scope"]={"target_id":target_id,"sonarr_episode":sonarr_episode}
    return digest(identity)


def manual_approved_execution_key(item,target_id,approval,*,sonarr_episode=None):
    """Bind a manual execution to the exact target and persisted approval evidence."""
    identity={"item_id":item["id"],"snapshot_digest":item["digest"],
        "approved_revision":item["revision"],"manual_target_id":target_id,
        "manual_approval_digest":digest(approval)}
    if sonarr_episode is not None:
        if not isinstance(sonarr_episode,int) or isinstance(sonarr_episode,bool) or sonarr_episode<1:
            raise ValueError("Manual execution scope requires a positive Sonarr episode")
        identity["sonarr_episode"]=sonarr_episode
    return digest(identity)


def mapping_signature(snapshot):
    """Digest only the effective decision, not volatile/unselected provider metadata."""
    targets=[{"target_id":t.get("target_id"),"season_number":t.get("season_number")}
        for t in snapshot.get("targets",[]) if isinstance(t,dict)]
    return digest({
        "initial_state":snapshot.get("initial_state"),
        "targets":targets,
        "proposed_plan":snapshot.get("proposed_plan"),
        "reason_codes":snapshot.get("reason_codes") or [],
    })


class ApplicationStore(ReviewRepository):
    def __init__(self,path):
        super().__init__(path)
        self._runtime_capability={"ready":False,"status":"disabled","diagnostics":[]}
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS v4_app_meta (version INTEGER NOT NULL)")
            version=db.execute("SELECT version FROM v4_app_meta").fetchone()
            if version and version[0]!=1:raise ValueError("Unsupported application storage schema")
            if not version:db.execute("INSERT INTO v4_app_meta VALUES (1)")
            db.execute("CREATE TABLE IF NOT EXISTS v4_items (id TEXT PRIMARY KEY, original TEXT NOT NULL, digest TEXT NOT NULL, mapping_state TEXT NOT NULL, review_state TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, human_decision TEXT)")
            db.execute("CREATE TRIGGER IF NOT EXISTS v4_item_immutable BEFORE UPDATE OF original,digest,created_at ON v4_items BEGIN SELECT RAISE(ABORT,'Original application snapshot is immutable'); END")
            db.execute("CREATE TRIGGER IF NOT EXISTS v4_item_no_delete BEFORE DELETE ON v4_items BEGIN SELECT RAISE(ABORT,'Application history cannot be deleted'); END")
            db.execute("CREATE TABLE IF NOT EXISTS v4_current_targets (target_id TEXT PRIMARY KEY,item_id TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS v4_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, action TEXT NOT NULL, actor TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL)")
            for operation in ("UPDATE","DELETE"):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS v4_events_no_{operation.lower()} BEFORE {operation} ON v4_events BEGIN SELECT RAISE(ABORT,'Audit events are append-only'); END")
            db.execute("CREATE TABLE IF NOT EXISTS v4_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS v4_season_overrides (
                target_id TEXT PRIMARY KEY,
                source_url TEXT,
                ignore_errors INTEGER NOT NULL CHECK(ignore_errors IN (0,1)),
                note TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source_urls TEXT NOT NULL DEFAULT '[]',
                audio TEXT,
                manual_approved INTEGER NOT NULL DEFAULT 0,
                excluded INTEGER NOT NULL DEFAULT 0,
                episode_map TEXT NOT NULL DEFAULT '[]',
                approval TEXT
            )""")
            override_columns={row["name"] for row in db.execute("PRAGMA table_info(v4_season_overrides)")}
            for name,ddl in (
                ("source_urls","source_urls TEXT NOT NULL DEFAULT '[]'"),("audio","audio TEXT"),
                ("manual_approved","manual_approved INTEGER NOT NULL DEFAULT 0"),
                ("excluded","excluded INTEGER NOT NULL DEFAULT 0"),("episode_map","episode_map TEXT NOT NULL DEFAULT '[]'"),
                ("approval","approval TEXT"),
            ):
                if name not in override_columns:db.execute(f"ALTER TABLE v4_season_overrides ADD COLUMN {ddl}")
            db.execute("""CREATE TABLE IF NOT EXISTS v4_series_audio_master (
                series_id TEXT PRIMARY KEY,
                audio TEXT NOT NULL CHECK(audio IN ('SUB','DUB')),
                source_target_id TEXT NOT NULL,
                source_item_digest TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS v4_provider_validation_state (
                item_id TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK(state IN ('current','pending','stale_retry','invalidated')),
                checked_at TEXT NOT NULL,
                details TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS v4_notification_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_sequence INTEGER NOT NULL UNIQUE,
                item_id TEXT,
                topic TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                payload TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS v4_notification_cursors (
                backend TEXT PRIMARY KEY,
                sequence INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS v4_notification_delivery_state (
                backend TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                last_sent_at TEXT NOT NULL,
                last_sequence INTEGER NOT NULL,
                PRIMARY KEY(backend,fingerprint)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS v4_notification_retry_state (
                backend TEXT PRIMARY KEY,
                sequence INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                next_attempt_at TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL,
                last_error_category TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("CREATE TABLE IF NOT EXISTS v4_judge_cache (input_hash TEXT PRIMARY KEY,result TEXT NOT NULL,created_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS v4_judge_attempts (item_id TEXT NOT NULL,input_hash TEXT NOT NULL,created_at TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(item_id,input_hash),FOREIGN KEY(item_id) REFERENCES v4_items(id))")
            db.execute("CREATE TRIGGER IF NOT EXISTS v4_judge_attempt_immutable BEFORE UPDATE ON v4_judge_attempts BEGIN SELECT RAISE(ABORT,'Judge attempt is immutable'); END")
            db.execute("CREATE TRIGGER IF NOT EXISTS v4_judge_attempt_no_delete BEFORE DELETE ON v4_judge_attempts BEGIN SELECT RAISE(ABORT,'Judge history cannot be deleted'); END")
            db.execute("CREATE TABLE IF NOT EXISTS v4_writer_claims (target_id TEXT PRIMARY KEY,owner TEXT NOT NULL CHECK(owner IN ('v3','v4')),owner_token TEXT NOT NULL,claimed_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS v4_execution_attempts (execution_key TEXT PRIMARY KEY,item_id TEXT NOT NULL,snapshot_digest TEXT NOT NULL,approved_revision INTEGER NOT NULL,status TEXT NOT NULL CHECK(status IN ('reserved','completed','failed')),created_at TEXT NOT NULL,finished_at TEXT,receipt TEXT,error_category TEXT,FOREIGN KEY(item_id) REFERENCES v4_items(id))")
            db.execute("CREATE TABLE IF NOT EXISTS v4_execution_payloads (execution_key TEXT PRIMARY KEY,envelope TEXT NOT NULL,FOREIGN KEY(execution_key) REFERENCES v4_execution_attempts(execution_key))")
            db.execute("CREATE TRIGGER IF NOT EXISTS v4_execution_payload_immutable BEFORE UPDATE ON v4_execution_payloads BEGIN SELECT RAISE(ABORT,'Execution envelope is immutable'); END")
            db.execute("CREATE TRIGGER IF NOT EXISTS v4_execution_payload_no_delete BEFORE DELETE ON v4_execution_payloads BEGIN SELECT RAISE(ABORT,'Execution envelope cannot be deleted'); END")
            # Phase 2 scopes live attempts to one wanted episode. The immutable
            # execution key remains unique; a snapshot may therefore have several
            # distinct, independently journaled episode effects.
            db.execute("DROP INDEX IF EXISTS v4_execution_once_per_snapshot")
            db.execute("""CREATE TRIGGER IF NOT EXISTS v4_execution_attempt_one_transition BEFORE UPDATE ON v4_execution_attempts
                WHEN OLD.status!='reserved' OR NEW.status NOT IN ('completed','failed')
                  OR NEW.execution_key IS NOT OLD.execution_key OR NEW.item_id IS NOT OLD.item_id
                  OR NEW.snapshot_digest IS NOT OLD.snapshot_digest OR NEW.approved_revision IS NOT OLD.approved_revision
                  OR NEW.created_at IS NOT OLD.created_at OR NEW.finished_at IS NULL
                  OR (NEW.status='completed' AND (NEW.receipt IS NULL OR NEW.error_category IS NOT NULL))
                  OR (NEW.status='failed' AND (NEW.receipt IS NOT NULL OR NEW.error_category IS NULL))
                BEGIN SELECT RAISE(ABORT,'Execution outcome is immutable'); END""")
            db.execute("CREATE TRIGGER IF NOT EXISTS v4_execution_attempt_no_delete BEFORE DELETE ON v4_execution_attempts BEGIN SELECT RAISE(ABORT,'Execution history cannot be deleted'); END")
            # Phase 3 read model.  Immutable v4_items remain the audit authority;
            # these tables are rebuildable, transactional current projections.
            db.execute("CREATE TABLE IF NOT EXISTS v4_projection_meta (version INTEGER NOT NULL)")
            projection_version=db.execute("SELECT version FROM v4_projection_meta").fetchone()
            if projection_version and projection_version[0] not in {1,2,3}:
                raise ValueError("Unsupported current projection schema")
            if not projection_version:
                db.execute("INSERT INTO v4_projection_meta VALUES (3)")
            elif projection_version[0]<3:
                db.execute("UPDATE v4_projection_meta SET version=3")
            db.execute("""CREATE TABLE IF NOT EXISTS v4_current_series (
                series_id INTEGER PRIMARY KEY,title TEXT NOT NULL,status TEXT NOT NULL,
                season_count INTEGER NOT NULL,episode_count INTEGER NOT NULL,
                mapped_episode_count INTEGER NOT NULL,attention_count INTEGER NOT NULL,
                updated_at TEXT,summary TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS v4_current_seasons (
                target_id TEXT PRIMARY KEY,series_id INTEGER NOT NULL,season INTEGER NOT NULL,
                item_id TEXT NOT NULL,detail TEXT NOT NULL,
                FOREIGN KEY(series_id) REFERENCES v4_current_series(series_id) ON DELETE CASCADE
            )""")
            db.execute("CREATE TABLE IF NOT EXISTS v4_target_inventory (target_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,observed_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS v4_item_titles (item_id TEXT PRIMARY KEY,title TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS v4_item_summaries (
                item_id TEXT PRIMARY KEY,mapping_state TEXT NOT NULL,review_state TEXT NOT NULL,
                created_at TEXT NOT NULL,summary TEXT NOT NULL)""")
            db.execute("""INSERT OR IGNORE INTO v4_item_titles(item_id,title)
                SELECT id,COALESCE(json_extract(original,'$.targets[0].canonical_title'),'') FROM v4_items""")
            db.execute("CREATE INDEX IF NOT EXISTS v4_items_state_created ON v4_items(mapping_state,created_at,id)")
            db.execute("CREATE INDEX IF NOT EXISTS v4_items_review_created ON v4_items(review_state,created_at,id)")
            db.execute("CREATE INDEX IF NOT EXISTS v4_current_targets_item ON v4_current_targets(item_id,target_id)")
            db.execute("CREATE INDEX IF NOT EXISTS v4_events_action_sequence ON v4_events(action,sequence DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS v4_notification_topic_sequence ON v4_notification_events(topic,sequence)")
            db.execute("CREATE INDEX IF NOT EXISTS v4_provider_validation_state_checked ON v4_provider_validation_state(state,checked_at,item_id)")
            db.execute("CREATE INDEX IF NOT EXISTS v4_current_series_title ON v4_current_series(title COLLATE NOCASE,series_id)")
            db.execute("CREATE INDEX IF NOT EXISTS v4_current_series_status_title ON v4_current_series(status,title COLLATE NOCASE,series_id)")
            db.execute("CREATE INDEX IF NOT EXISTS v4_current_seasons_series ON v4_current_seasons(series_id,season,target_id)")
            db.execute("CREATE INDEX IF NOT EXISTS v4_item_summaries_state_created ON v4_item_summaries(mapping_state,created_at,item_id)")
            db.execute("CREATE INDEX IF NOT EXISTS v4_item_summaries_review_created ON v4_item_summaries(review_state,created_at,item_id)")
            for row in db.execute("""SELECT i.* FROM v4_items i LEFT JOIN v4_item_summaries s
                ON s.item_id=i.id WHERE s.item_id IS NULL""").fetchall():
                self._write_item_summary(db,self._decode(row))
            projected={row[0] for row in db.execute("SELECT target_id FROM v4_current_seasons")}
            current={row[0] for row in db.execute("SELECT target_id FROM v4_current_targets")}
            if projected!=current:
                self._refresh_current_projections(db)

    def _decode(self,row):
        if row is None:raise KeyError("Unknown mapping/review")
        item=dict(row)
        if digest(json.loads(item["original"]))!=item["digest"]:raise ValueError("Snapshot integrity failure")
        item["original"]=json.loads(item["original"])
        item["human_decision"]=json.loads(item["human_decision"]) if item["human_decision"] else None
        return item

    @staticmethod
    def _write_item_summary(db,item):
        from .application_dto import item_list_dto
        db.execute("""INSERT INTO v4_item_summaries VALUES (?,?,?,?,?)
            ON CONFLICT(item_id) DO UPDATE SET mapping_state=excluded.mapping_state,
            review_state=excluded.review_state,created_at=excluded.created_at,summary=excluded.summary""",
            (item["id"],item["mapping_state"],item["review_state"],item["created_at"],
             encode(item_list_dto(item))))

    def get_item(self,id):
        with self._connect() as db:return self._decode(db.execute("SELECT * FROM v4_items WHERE id=?",(id,)).fetchone())

    def get_items(self,ids):
        ids=list(dict.fromkeys(str(value) for value in ids))
        if not ids:return {}
        placeholders=",".join("?" for _ in ids)
        with self._connect() as db:
            rows=db.execute(f"SELECT * FROM v4_items WHERE id IN ({placeholders})",tuple(ids)).fetchall()
        return {row["id"]:self._decode(row) for row in rows}

    def judge_cache_get(self,key):
        with self._connect() as db:
            row=db.execute("SELECT result FROM v4_judge_cache WHERE input_hash=?",(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def judge_cache_put(self,key,result):
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO v4_judge_cache VALUES (?,?,?)",(key,encode(result),self._now()))

    def judge_attempt(self,id):
        with self._connect() as db:
            row=db.execute("SELECT created_at,result FROM v4_judge_attempts WHERE item_id=? ORDER BY created_at DESC LIMIT 1",(id,)).fetchone()
        return {**json.loads(row[1]),"timestamp":row[0]} if row else None

    def record_judge_attempt(self,id,result):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item=self._decode(db.execute("SELECT * FROM v4_items WHERE id=?",(id,)).fetchone())
            if item["mapping_state"]!="needs_review" or item["review_state"]!="open":return False
            now=self._now()
            cursor=db.execute("INSERT OR IGNORE INTO v4_judge_attempts VALUES (?,?,?,?)",
                (id,result["input_hash"],now,encode(result)))
            if cursor.rowcount:self._event(db,id,"judge_suggestion" if result["status"]=="suggested" else "judge_fallback",
                {"provider":result["provider"],"model":result["model"],"candidate_id":result["suggested_candidate_id"],
                 "confidence":result["confidence"],"fallback":result["fallback"],"input_hash":result["input_hash"]},"shadow_scan")
        return bool(cursor.rowcount)

    def list_items(self):
        with self._connect() as db:return [self._decode(r) for r in db.execute("SELECT * FROM v4_items ORDER BY created_at,id")]

    def current_items(self):
        """Decode current immutable items only; history is available via list_items."""
        with self._connect() as db:
            rows=db.execute("""SELECT i.* FROM v4_items i
                WHERE EXISTS (SELECT 1 FROM v4_current_targets t WHERE t.item_id=i.id)
                ORDER BY i.created_at,i.id""").fetchall()
        return [self._decode(row) for row in rows]

    def list_items_page(self,*,reviews=False,state=None,offset=0,limit=50):
        """SQL-filter and paginate before immutable JSON validation/decoding."""
        if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:
            raise ValueError("Invalid offset")
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100:
            raise ValueError("Invalid limit")
        where=[];parameters=[]
        if reviews:
            where.append("review_state!='not_required'")
            if state is not None:
                where.append("review_state=?");parameters.append(state)
        elif state is not None:
            where.append("mapping_state=?");parameters.append(state)
        clause=(" WHERE "+" AND ".join(where)) if where else ""
        with self._connect() as db:
            total=int(db.execute("SELECT COUNT(*) FROM v4_items"+clause,parameters).fetchone()[0])
            rows=db.execute("SELECT * FROM v4_items"+clause+" ORDER BY created_at,id LIMIT ? OFFSET ?",
                (*parameters,limit,offset)).fetchall()
        return [self._decode(row) for row in rows],total

    def list_item_summaries_page(self,*,reviews=False,state=None,offset=0,limit=50):
        """Read list DTOs without reconstructing immutable snapshot JSON."""
        if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:
            raise ValueError("Invalid offset")
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100:
            raise ValueError("Invalid limit")
        where=[];parameters=[]
        if reviews:
            where.append("review_state!='not_required'")
            if state is not None:where.append("review_state=?");parameters.append(state)
        elif state is not None:
            where.append("mapping_state=?");parameters.append(state)
        clause=(" WHERE "+" AND ".join(where)) if where else ""
        with self._connect() as db:
            total=int(db.execute("SELECT COUNT(*) FROM v4_item_summaries"+clause,parameters).fetchone()[0])
            rows=db.execute("SELECT summary FROM v4_item_summaries"+clause+
                " ORDER BY created_at,item_id LIMIT ? OFFSET ?",(*parameters,limit,offset)).fetchall()
        return [json.loads(row[0]) for row in rows],total

    def overview_counts(self):
        with self._connect() as db:
            states={row["mapping_state"]:int(row["amount"]) for row in db.execute(
                """SELECT i.mapping_state,COUNT(DISTINCT i.id) amount
                FROM v4_items i JOIN v4_current_targets t ON t.item_id=i.id
                GROUP BY i.mapping_state""")}
            open_reviews=int(db.execute("""SELECT COUNT(DISTINCT i.id) FROM v4_items i
                JOIN v4_current_targets t ON t.item_id=i.id WHERE i.review_state='open'""").fetchone()[0])
            current_targets=int(db.execute("SELECT COUNT(*) FROM v4_current_targets").fetchone()[0])
        return states,open_reviews,current_targets

    def _refresh_current_projections(self,db,series_ids=None):
        """Rebuild affected current series inside the caller's transaction."""
        from .series_view import series_list

        selected=None if series_ids is None else {int(value) for value in series_ids}
        if selected is None:
            db.execute("DELETE FROM v4_current_seasons")
            db.execute("DELETE FROM v4_current_series")
            rows=db.execute("""SELECT i.* FROM v4_items i WHERE EXISTS
                (SELECT 1 FROM v4_current_targets t WHERE t.item_id=i.id)
                ORDER BY i.created_at,i.id""").fetchall()
        else:
            if not selected:return
            placeholders=",".join("?" for _ in selected);values=tuple(sorted(selected))
            db.execute(f"DELETE FROM v4_current_seasons WHERE series_id IN ({placeholders})",values)
            db.execute(f"DELETE FROM v4_current_series WHERE series_id IN ({placeholders})",values)
            conditions=" OR ".join("t.target_id LIKE ?" for _ in values)
            rows=db.execute(f"""SELECT DISTINCT i.* FROM v4_items i
                JOIN v4_current_targets t ON t.item_id=i.id
                WHERE {conditions} ORDER BY i.created_at,i.id""",tuple(f"{value}:%" for value in values)).fetchall()
        items=[self._decode(row) for row in rows]
        overrides={row["target_id"]:self._override_row(row) for row in db.execute("""SELECT
            target_id,source_url,source_urls,audio,ignore_errors,manual_approved,
            excluded,episode_map,approval,note,updated_at FROM v4_season_overrides""")}
        validation_states={row["item_id"]:{"state":row["state"],"checked_at":row["checked_at"],
            "details":json.loads(row["details"])} for row in db.execute("SELECT * FROM v4_provider_validation_state")}
        for row in series_list(items,overrides,validation_states):
            summary={key:value for key,value in row.items() if key!="seasons"}
            season_summary_keys=("season","target_id","episode_count","mapped_episode_count",
                "coverage_percent","mapping_id","mapping_state","review_state","updated_at",
                "automation","auto_ready","can_execute","provider_validation_state","attention",
                "reason_codes","mapping_bases","audio_preference")
            summary["seasons"]=[{key:season.get(key) for key in season_summary_keys} for season in row["seasons"]]
            db.execute("INSERT INTO v4_current_series VALUES (?,?,?,?,?,?,?,?,?)",(
                row["series_id"],row["title"],row["status"],row["season_count"],row["episode_count"],
                row["mapped_episode_count"],row["attention_count"],row.get("updated_at"),encode(summary)))
            for season in row["seasons"]:
                db.execute("INSERT INTO v4_current_seasons VALUES (?,?,?,?,?)",(
                    season["target_id"],row["series_id"],season["season"],season["mapping_id"],encode(season)))

    def series_summaries(self,*,offset=0,limit=200,query=None,status=None):
        if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:
            raise ValueError("Invalid offset")
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=500:
            raise ValueError("Invalid limit")
        where=[];parameters=[]
        if query:
            where.append("title LIKE ? ESCAPE '\\' COLLATE NOCASE")
            escaped=str(query).replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
            parameters.append(f"%{escaped}%")
        if status:
            where.append("status=?");parameters.append(str(status))
        clause=(" WHERE "+" AND ".join(where)) if where else ""
        with self._connect() as db:
            total=int(db.execute("SELECT COUNT(*) FROM v4_current_series"+clause,parameters).fetchone()[0])
            rows=db.execute("SELECT summary FROM v4_current_series"+clause+
                " ORDER BY title COLLATE NOCASE,series_id LIMIT ? OFFSET ?",(*parameters,limit,offset)).fetchall()
        return [json.loads(row[0]) for row in rows],total

    def series_projection(self,series_id):
        series_id=int(series_id)
        with self._connect() as db:
            row=db.execute("SELECT summary FROM v4_current_series WHERE series_id=?",(series_id,)).fetchone()
            if row is None:raise KeyError("Unknown series")
            seasons=[json.loads(value[0]) for value in db.execute(
                "SELECT detail FROM v4_current_seasons WHERE series_id=? ORDER BY season,target_id",(series_id,))]
        return {**json.loads(row[0]),"seasons":seasons,
            "source_names":sorted({source.get("title") for season in seasons
                for source in season.get("effective",{}).get("sources",[]) if source.get("title")})}

    def current_item_for_target(self,target_id):
        with self._connect() as db:
            row=db.execute("""SELECT i.* FROM v4_items i JOIN v4_current_targets t
                ON t.item_id=i.id WHERE t.target_id=?""",(str(target_id),)).fetchone()
        return self._decode(row)

    def target_inventory(self):
        with self._connect() as db:
            return {row["target_id"]:row["fingerprint"] for row in db.execute(
                "SELECT target_id,fingerprint FROM v4_target_inventory ORDER BY target_id")}

    def record_target_inventory(self,values):
        if not isinstance(values,dict):raise ValueError("Target inventory must be an object")
        now=self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current={row[0] for row in db.execute("SELECT target_id FROM v4_current_targets")}
            for target_id,fingerprint in sorted(values.items()):
                if target_id not in current:continue
                if not isinstance(fingerprint,str) or len(fingerprint)!=64:raise ValueError("Invalid target fingerprint")
                db.execute("""INSERT INTO v4_target_inventory VALUES (?,?,?)
                    ON CONFLICT(target_id) DO UPDATE SET fingerprint=excluded.fingerprint,
                    observed_at=excluded.observed_at""",(target_id,fingerprint,now))
            if current:
                placeholders=",".join("?" for _ in current)
                db.execute(f"DELETE FROM v4_target_inventory WHERE target_id NOT IN ({placeholders})",tuple(sorted(current)))
            else:
                db.execute("DELETE FROM v4_target_inventory")

    def projection_query_plans(self):
        with self._connect() as db:
            series=" ".join(str(row[3]) for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT summary FROM v4_current_series ORDER BY title COLLATE NOCASE,series_id LIMIT 50"))
            items=" ".join(str(row[3]) for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT summary FROM v4_item_summaries WHERE mapping_state='proposed' ORDER BY created_at,item_id LIMIT 50"))
            activity=" ".join(str(row[3]) for row in db.execute("""EXPLAIN QUERY PLAN
                SELECT e.sequence,t.title FROM v4_events e
                LEFT JOIN v4_item_titles t ON t.item_id=e.item_id
                WHERE e.action IN ('scan_completed') ORDER BY e.sequence DESC LIMIT 50"""))
        return {"series":series,"items":items,"activity":activity}

    def series_audio_masters(self):
        with self._connect() as db:
            rows=db.execute("SELECT * FROM v4_series_audio_master ORDER BY series_id").fetchall()
        return {row["series_id"]:dict(row) for row in rows}

    def set_series_audio_master(self,series_id,audio,source_target_id,source_item_digest,*,actor="shadow_scan"):
        series_id=str(series_id)
        if audio not in {"SUB","DUB"}:raise ValueError("Series audio master must be SUB or DUB")
        if not source_target_id or not source_item_digest:raise ValueError("Series audio master requires source evidence")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT audio,revision FROM v4_series_audio_master WHERE series_id=?",(series_id,)).fetchone()
            revision=(int(row["revision"])+1) if row and row["audio"]!=audio else int(row["revision"]) if row else 0
            now=self._now()
            db.execute("""INSERT INTO v4_series_audio_master VALUES (?,?,?,?,?,?)
                ON CONFLICT(series_id) DO UPDATE SET audio=excluded.audio,
                source_target_id=excluded.source_target_id,source_item_digest=excluded.source_item_digest,
                revision=excluded.revision,updated_at=excluded.updated_at""",
                (series_id,audio,source_target_id,source_item_digest,revision,now))
            if not row or row["audio"]!=audio:
                self._event(db,None,"series_audio_master_changed",{"series_id":series_id,"audio":audio,
                    "source_target_id":source_target_id,"revision":revision},actor)
        return self.series_audio_masters()[series_id]

    def record_provider_validation_state(self,item_ids,state,details=None):
        if state not in {"current","pending","stale_retry","invalidated"}:raise ValueError("Invalid provider validation state")
        item_ids=sorted(set(item_ids));now=self._now();payload=encode(details or {})
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item_id in item_ids:
                db.execute("""INSERT INTO v4_provider_validation_state VALUES (?,?,?,?)
                    ON CONFLICT(item_id) DO UPDATE SET state=excluded.state,checked_at=excluded.checked_at,
                    details=excluded.details""",(item_id,state,now,payload))
            if item_ids:
                placeholders=",".join("?" for _ in item_ids)
                series_ids={int(str(row[0]).split(":",1)[0]) for row in db.execute(
                    f"SELECT target_id FROM v4_current_targets WHERE item_id IN ({placeholders})",tuple(item_ids))}
                self._refresh_current_projections(db,series_ids)

    def provider_validation_states(self):
        with self._connect() as db:
            rows=db.execute("SELECT * FROM v4_provider_validation_state ORDER BY item_id").fetchall()
        return {row["item_id"]:{"state":row["state"],"checked_at":row["checked_at"],
            "details":json.loads(row["details"])} for row in rows}

    @staticmethod
    def _validate_claim(target_ids,owner,owner_token):
        target_ids=tuple(target_ids)
        if not target_ids or len(set(target_ids))!=len(target_ids) or any(not isinstance(v,str) or not v for v in target_ids):
            raise ValueError("Writer claims require unique non-empty target IDs")
        if owner not in {"v3","v4"}:raise ValueError("Writer owner must be v3 or v4")
        if not isinstance(owner_token,str) or not owner_token or len(owner_token)>128:raise ValueError("Invalid writer owner token")
        return target_ids

    def _claim_writer(self,db,target_ids,owner,owner_token):
        target_ids=self._validate_claim(target_ids,owner,owner_token)
        for target_id in sorted(target_ids):
            current=db.execute("SELECT owner,owner_token FROM v4_writer_claims WHERE target_id=?",(target_id,)).fetchone()
            if current and (current["owner"],current["owner_token"])!=(owner,owner_token):
                raise Conflict(f"Target {target_id} is owned by another writer")
            if not current:
                db.execute("INSERT INTO v4_writer_claims VALUES (?,?,?,?)",(target_id,owner,owner_token,self._now()))

    def claim_writer(self,target_ids,owner,owner_token):
        """Atomically claim targets for a cooperating V3 or V4 writer."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._claim_writer(db,target_ids,owner,owner_token)

    def writer_claims(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT target_id,owner,claimed_at FROM v4_writer_claims ORDER BY target_id")]

    def transfer_writer(self,target_ids,from_owner,from_token,to_owner,to_token,attestation):
        target_ids=self._validate_claim(target_ids,to_owner,to_token)
        self._validate_claim(target_ids,from_owner,from_token)
        if not isinstance(attestation,dict):raise ValueError("Cutover attestation is required")
        base={"verified_by","reason","verification_method"}
        expected=base|({"v3_stopped"} if (from_owner,to_owner)==("v3","v4") else {"rollback_authorized"} if (from_owner,to_owner)==("v4","v3") else set())
        if set(attestation)!=expected:raise ValueError("Invalid cutover attestation fields")
        if not all(isinstance(attestation.get(k),str) and 1<=len(attestation[k].strip())<=500 for k in base):raise ValueError("Invalid cutover attestation")
        if (from_owner,to_owner)==("v3","v4") and (attestation["v3_stopped"] is not True or attestation["verification_method"] not in {"service_stopped","shared_guard"}):
            raise Conflict("V3 stop/shared-guard verification is required")
        if (from_owner,to_owner)==("v4","v3") and (attestation["rollback_authorized"] is not True or attestation["verification_method"]!="operator_rollback"):
            raise Conflict("Explicit rollback authorization is required")
        if from_owner==to_owner:raise ValueError("Writer transfer must change owner")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for target_id in target_ids:
                row=db.execute("SELECT owner,owner_token FROM v4_writer_claims WHERE target_id=?",(target_id,)).fetchone()
                if not row or (row["owner"],row["owner_token"])!=(from_owner,from_token):raise Conflict("Writer ownership changed or is incomplete")
            if (from_owner,to_owner)==("v4","v3"):
                for row in db.execute("SELECT i.original FROM v4_execution_attempts a JOIN v4_items i ON i.id=a.item_id WHERE a.status='reserved'"):
                    active={target["target_id"] for target in json.loads(row[0])["targets"]}
                    if active&set(target_ids):raise Conflict("Reserved execution blocks writer rollback")
            now=self._now()
            for target_id in target_ids:
                db.execute("UPDATE v4_writer_claims SET owner=?,owner_token=?,claimed_at=? WHERE target_id=?",(to_owner,to_token,now,target_id))
            self._event(db,None,"writer_transferred",{"target_ids":target_ids,"from_owner":from_owner,"to_owner":to_owner,"verified_by":attestation["verified_by"].strip(),"reason":attestation["reason"].strip(),"verification_method":attestation["verification_method"]},"cutover-operator")

    def reserve_execution(self,id,expected_revision,owner_token,envelope_builder,*,require_preclaimed=False,
        automatic=False,key_authorizer=None,target_id=None,sonarr_episode=None):
        """Atomically reserve one current manual-approved or deterministic automatic snapshot."""
        if not isinstance(expected_revision,int) or isinstance(expected_revision,bool) or expected_revision<0:
            raise ValueError("expected_revision must be a nonnegative integer")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item=self._decode(db.execute("SELECT * FROM v4_items WHERE id=?",(id,)).fetchone())
            if automatic:
                if item["mapping_state"]!="proposed" or item["review_state"]!="not_required" or item["human_decision"] is not None:
                    raise Conflict("Automatic execution requires a current deterministic proposal")
            else:
                if item["mapping_state"]!="approved" or item["review_state"]!="resolved":
                    raise Conflict("Only a current approved plan may execute")
                human=item["human_decision"] or {}
                if human.get("action") not in {"approve","choose"}:
                    raise Conflict("Execution requires an explicit human approval")
            if item["revision"]!=expected_revision:
                raise Conflict("Stale execution revision")
            all_target_ids=tuple(target["target_id"] for target in item["original"]["targets"])
            scoped=target_id is not None or sonarr_episode is not None
            if scoped:
                approved_execution_key(item,target_id=target_id,sonarr_episode=sonarr_episode)
                if target_id not in all_target_ids:raise Conflict("Execution scope is not in the immutable snapshot")
            target_ids=(target_id,) if scoped else all_target_ids
            if automatic:
                placeholders=",".join("?" for _ in target_ids)
                manual_sql="""SELECT 1 FROM v4_season_overrides
                    WHERE target_id IN ({})
                      AND (source_url IS NOT NULL OR source_urls<>'[]' OR ignore_errors=1
                           OR manual_approved=1 OR excluded=1 OR episode_map<>'[]' OR TRIM(note)<>'')
                    LIMIT 1""".format(placeholders)
                if db.execute(manual_sql,target_ids).fetchone():
                    raise Conflict("Manual season override blocks automatic execution")
            owners={row[0] for current_target in all_target_ids for row in db.execute(
                "SELECT item_id FROM v4_current_targets WHERE target_id=?",(current_target,))}
            if owners!={id}:
                raise Conflict("Approved plan is not current for every target")
            validation=db.execute("SELECT state FROM v4_provider_validation_state WHERE item_id=?",(id,)).fetchone()
            if validation and validation["state"]!="current":
                raise Conflict("Provider validation is not current")
            automatic_audio=None
            if automatic:
                marks=",".join("?" for _ in target_ids)
                audio_values={row[0] for row in db.execute(
                    f"SELECT audio FROM v4_season_overrides WHERE target_id IN ({marks}) AND audio IS NOT NULL",
                    target_ids)}
                if len(audio_values)>1:raise Conflict("Automatic execution has conflicting season audio preferences")
                automatic_audio=next(iter(audio_values),None)
            execution_key=approved_execution_key(item,audio_preference=automatic_audio,
                target_id=target_id,sonarr_episode=sonarr_episode)
            if key_authorizer is not None:key_authorizer(execution_key)
            if db.execute("SELECT 1 FROM v4_execution_attempts WHERE execution_key=?",(execution_key,)).fetchone():
                raise Conflict("This exact execution already has an attempt")
            envelope=json.loads(encode(envelope_builder(item,execution_key,automatic_audio,target_id,sonarr_episode)
                if automatic else envelope_builder(item,execution_key,target_id,sonarr_episode)))
            if tuple(envelope.get("target_ids",()))!=target_ids:
                raise ValueError("Execution envelope target set differs from approved plan")
            if require_preclaimed:
                for target_id in target_ids:
                    claim=db.execute("SELECT owner,owner_token FROM v4_writer_claims WHERE target_id=?",(target_id,)).fetchone()
                    if not claim or (claim["owner"],claim["owner_token"])!=("v4",owner_token):raise Conflict("Exact preclaimed V4 ownership is required")
            else:self._claim_writer(db,target_ids,"v4",owner_token)
            now=self._now()
            db.execute("INSERT INTO v4_execution_attempts VALUES (?,?,?,?,?,?,?,?,?)",
                (execution_key,id,item["digest"],item["revision"],"reserved",now,None,None,None))
            db.execute("INSERT INTO v4_execution_payloads VALUES (?,?)",(execution_key,encode(envelope)))
            self._event(db,id,"execution_reserved",{"execution_key":execution_key,"snapshot_digest":item["digest"],"revision":item["revision"],"target_ids":target_ids},"v4-executor")
        return envelope

    def reserve_manual_execution(self,id,target_id,expected_revision,owner_token,envelope_builder,
        *,override_digest,current_sources,require_preclaimed=False,key_authorizer=None,sonarr_episode=None):
        """Reserve a live-revalidated manual season approval in one transaction."""
        if not isinstance(expected_revision,int) or isinstance(expected_revision,bool) or expected_revision<0:
            raise ValueError("expected_revision must be a nonnegative integer")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item=self._decode(db.execute("SELECT * FROM v4_items WHERE id=?",(id,)).fetchone())
            if item["revision"]!=expected_revision or item["mapping_state"]=="superseded":
                raise Conflict("Manual execution revision is stale")
            target=next((value for value in item["original"]["targets"] if value.get("target_id")==target_id),None)
            if target is None:raise Conflict("Manual execution target is not in the immutable snapshot")
            current=db.execute("SELECT item_id FROM v4_current_targets WHERE target_id=?",(target_id,)).fetchone()
            if not current or current["item_id"]!=id:raise Conflict("Manual execution target is not current")
            row=db.execute("SELECT * FROM v4_season_overrides WHERE target_id=?",(target_id,)).fetchone()
            if row is None:raise Conflict("Manual execution approval is unavailable")
            override=self._override_row(row)
            from .series_view import manual_approval_current
            if not manual_approval_current(item,target,override) or digest(override)!=override_digest:
                raise Conflict("Manual execution approval changed before reservation")
            target_ids=(target_id,)
            execution_key=manual_approved_execution_key(item,target_id,override["approval"],
                sonarr_episode=sonarr_episode)
            if key_authorizer is not None:key_authorizer(execution_key)
            if db.execute("SELECT 1 FROM v4_execution_attempts WHERE execution_key=?",(execution_key,)).fetchone():
                raise Conflict("This exact execution already has an attempt")
            envelope=json.loads(encode(envelope_builder(item,target,override,current_sources,execution_key,
                sonarr_episode)))
            if tuple(envelope.get("target_ids",()))!=target_ids:
                raise ValueError("Manual execution envelope target set differs from atomic plan")
            if require_preclaimed:
                for atomic_target in target_ids:
                    claim=db.execute("SELECT owner,owner_token FROM v4_writer_claims WHERE target_id=?",(atomic_target,)).fetchone()
                    if not claim or (claim["owner"],claim["owner_token"])!=("v4",owner_token):
                        raise Conflict("Exact preclaimed V4 ownership is required")
            else:self._claim_writer(db,target_ids,"v4",owner_token)
            now=self._now()
            db.execute("INSERT INTO v4_execution_attempts VALUES (?,?,?,?,?,?,?,?,?)",
                (execution_key,id,item["digest"],item["revision"],"reserved",now,None,None,None))
            db.execute("INSERT INTO v4_execution_payloads VALUES (?,?)",(execution_key,encode(envelope)))
            self._event(db,id,"execution_reserved",{"execution_key":execution_key,
                "snapshot_digest":item["digest"],"revision":item["revision"],
                "target_ids":target_ids,"manual_target_id":target_id},"v4-executor")
        return envelope

    def reserved_execution(self,execution_key):
        with self._connect() as db:
            row=db.execute("SELECT a.*,p.envelope FROM v4_execution_attempts a JOIN v4_execution_payloads p USING(execution_key) WHERE a.execution_key=?",(execution_key,)).fetchone()
        if not row or row["status"]!="reserved":raise Conflict("Execution reservation is not open")
        value=dict(row);value["envelope"]=json.loads(value["envelope"]);return value

    def reconciliation_pending(self,execution_key,category):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT item_id,status FROM v4_execution_attempts WHERE execution_key=?",(execution_key,)).fetchone()
            if not row or row["status"]!="reserved":raise Conflict("Execution reservation is not open")
            self._event(db,row["item_id"],"execution_reconciliation_pending",{"execution_key":execution_key,"error_category":category},"v4-executor")

    def finish_execution(self,execution_key,*,receipt=None,error_category=None):
        if (receipt is None)==(error_category is None):raise ValueError("Exactly one execution outcome is required")
        status="completed" if receipt is not None else "failed"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT item_id,status FROM v4_execution_attempts WHERE execution_key=?",(execution_key,)).fetchone()
            if not row or row["status"]!="reserved":raise Conflict("Execution reservation is not open")
            now=self._now()
            db.execute("UPDATE v4_execution_attempts SET status=?,finished_at=?,receipt=?,error_category=? WHERE execution_key=?",
                (status,now,encode(receipt) if receipt is not None else None,error_category,execution_key))
            payload={"execution_key":execution_key,"status":status}
            if error_category:payload["error_category"]=error_category
            if receipt is not None:payload["receipt"] = receipt
            self._event(db,row["item_id"],f"execution_{status}",payload,"v4-executor")

    def execution_attempts(self):
        with self._connect() as db:
            rows=db.execute("SELECT * FROM v4_execution_attempts ORDER BY created_at,execution_key").fetchall()
        result=[]
        for row in rows:
            value=dict(row)
            value["receipt"]=json.loads(value["receipt"]) if value["receipt"] else None
            result.append(value)
        return result

    def reserved_executions(self):
        """Return immutable open reservations for startup reconciliation."""
        with self._connect() as db:
            rows=db.execute("""SELECT a.*,p.envelope FROM v4_execution_attempts a
                JOIN v4_execution_payloads p USING(execution_key)
                WHERE a.status='reserved' ORDER BY a.created_at,a.execution_key""").fetchall()
        result=[]
        for row in rows:
            value=dict(row);value["envelope"]=json.loads(value["envelope"]);result.append(value)
        return result

    def _event(self,db,item_id,action,payload,actor="local-user"):
        timestamp=self._now()
        cursor=db.execute("INSERT INTO v4_events(item_id,action,actor,timestamp,payload) VALUES (?,?,?,?,?)",
            (item_id,action,actor,timestamp,encode(payload)))
        topic=notification_topic(action,payload)
        if topic:
            db.execute("""INSERT OR IGNORE INTO v4_notification_events
                (audit_sequence,item_id,topic,timestamp,payload) VALUES (?,?,?,?,?)""",
                (cursor.lastrowid,item_id,topic,timestamp,encode({"action":action,**payload})))

    def record_scan(self,snapshots,dataset,*,authoritative=False):
        # Whole batch is atomic: a failed scan cannot partially supersede approved plans.
        ids=[];changed=0;scan_id=str(uuid.uuid4());affected_series=set()
        target_ids=[t["target_id"] for s in snapshots for t in s["targets"]]
        if len(set(target_ids))!=len(target_ids):raise ValueError("Scan has overlapping target groups")
        target_meta={t["target_id"]:(t.get("canonical_title"),t.get("season_number")) for s in snapshots for t in s["targets"]}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            target_set=set(target_ids)
            previous_target_set={r[0] for r in db.execute("SELECT target_id FROM v4_current_targets")}
            if authoritative:
                current={}
                for row in db.execute("SELECT target_id,item_id FROM v4_current_targets"):
                    current.setdefault(row["item_id"],set()).add(row["target_id"])
                # A full live scan is authoritative for eligibility. If Sonarr removes
                # the animeworld tag (or a target otherwise disappears), the old item
                # must stop being current instead of lingering in the UI/downloader.
                for owner,previous_targets in current.items():
                    if previous_targets<=target_set:continue
                    affected_series.update(int(str(target_id).split(":",1)[0]) for target_id in previous_targets)
                    previous=self._decode(db.execute("SELECT * FROM v4_items WHERE id=?",(owner,)).fetchone())
                    now=self._now()
                    db.execute("UPDATE v4_items SET mapping_state='superseded',review_state='superseded',revision=revision+1,updated_at=? WHERE id=?",(now,owner))
                    self._write_item_summary(db,self._decode(
                        db.execute("SELECT * FROM v4_items WHERE id=?",(owner,)).fetchone()))
                    db.execute("DELETE FROM v4_current_targets WHERE item_id=?",(owner,))
                    self._event(db,owner,"supersede",{"replacement_id":None,"scan_id":scan_id,
                        "previous_revision":previous["revision"],"reason":"removed_from_live_scan",
                        "target_ids":sorted(previous_targets-target_set)},"shadow_scan")
            for snapshot in snapshots:
                tids=[t["target_id"] for t in snapshot["targets"]];fingerprint=digest(snapshot)
                owners={r[0] for tid in tids for r in db.execute("SELECT item_id FROM v4_current_targets WHERE target_id=?",(tid,))}
                existing=self._decode(db.execute("SELECT * FROM v4_items WHERE id=?",(next(iter(owners)),)).fetchone()) if len(owners)==1 else None
                if existing and existing["digest"]==fingerprint:
                    ids.append(existing["id"]);continue
                id=str(uuid.uuid4());now=self._now();state=snapshot["initial_state"]
                affected_series.update(int(str(target_id).split(":",1)[0]) for target_id in tids)
                if state not in {"proposed","needs_review","waiting","airing","unavailable"}:raise ValueError("Invalid scan outcome")
                for owner in owners:
                    previous_targets={r[0] for r in db.execute("SELECT target_id FROM v4_current_targets WHERE item_id=?",(owner,))}
                    affected_series.update(int(str(target_id).split(":",1)[0]) for target_id in previous_targets)
                    if not previous_targets<=set(target_ids):raise Conflict("Partial scan cannot replace a multi-target plan")
                    previous=self._decode(db.execute("SELECT * FROM v4_items WHERE id=?",(owner,)).fetchone())
                    policy_terminal=(state=="unavailable" and
                        "required_audio_unavailable" in set(snapshot.get("reason_codes") or []))
                    if previous["mapping_state"]=="proposed" and state!="proposed" and not policy_terminal:
                        self._event(db,owner,"automatic_regression",{"replacement_id":id,"scan_id":scan_id,
                            "previous_state":"proposed","new_state":state,"target_ids":sorted(tids)},"shadow_scan")
                    elif (previous["mapping_state"]=="proposed" and state=="proposed"
                          and mapping_signature(previous["original"])!=mapping_signature(snapshot)):
                        self._event(db,owner,"mapping_changed",{"replacement_id":id,"scan_id":scan_id,
                            "target_ids":sorted(tids)},"shadow_scan")
                    db.execute("UPDATE v4_items SET mapping_state='superseded',review_state='superseded',revision=revision+1,updated_at=? WHERE id=?",(now,owner))
                    self._write_item_summary(db,self._decode(
                        db.execute("SELECT * FROM v4_items WHERE id=?",(owner,)).fetchone()))
                    db.execute("DELETE FROM v4_current_targets WHERE item_id=?",(owner,))
                    self._event(db,owner,"supersede",{"replacement_id":id,"scan_id":scan_id,"previous_revision":previous["revision"]},"shadow_scan")
                db.execute("INSERT INTO v4_items VALUES (?,?,?,?,?,?,?,?,?)",(id,encode(snapshot),fingerprint,state,"open" if state=="needs_review" else "not_required",0,now,now,None))
                db.execute("INSERT INTO v4_item_titles VALUES (?,?)",(
                    id,str((snapshot.get("targets") or [{}])[0].get("canonical_title") or "")))
                self._write_item_summary(db,self._decode(
                    db.execute("SELECT * FROM v4_items WHERE id=?",(id,)).fetchone()))
                for tid in tids:db.execute("INSERT INTO v4_current_targets VALUES (?,?)",(tid,id))
                event="proposal_created" if state=="proposed" else "review_created" if state=="needs_review" else "terminal_classified"
                self._event(db,id,event,{"scan_id":scan_id,"snapshot_digest":fingerprint,"state":state},"shadow_scan")
                ids.append(id);changed+=1
            # Do not notify the very first baseline import. On later authoritative
            # scans, a new Sonarr season target becomes a first-class event.
            if authoritative and previous_target_set:
                for target_id in sorted(target_set-previous_target_set):
                    title,season=target_meta.get(target_id,(None,None))
                    self._event(db,None,"new_season_detected",{"scan_id":scan_id,"target_id":target_id,
                        "title":title,"season":season},"shadow_scan")
            self._refresh_current_projections(db,affected_series)
            self._event(db,None,"scan_completed",{"scan_id":scan_id,"dataset":dataset,"targets":len(target_ids),"items":len(ids),"new_items":changed,"mode":"shadow","external_writes":False},"shadow_scan")
        return {"scan_id":scan_id,"status":"completed","mode":"shadow","target_count":len(target_ids),"item_count":len(ids),"new_items":changed,"item_ids":ids,"external_writes":False,"local_storage_written":True}

    def transition(self,id,action,revision,reason,decision=None):
        if not isinstance(revision,int) or isinstance(revision,bool) or revision<0:raise ValueError("expected_revision must be a nonnegative integer")
        if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:raise ValueError("A reason of 1..2000 characters is required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item=self._decode(db.execute("SELECT * FROM v4_items WHERE id=?",(id,)).fetchone())
            if item["revision"]!=revision or item["mapping_state"]=="superseded":raise Conflict("Stale revision or superseded item")
            if action=="reopen":
                if item["review_state"] not in {"resolved","rejected","dismissed"}:raise Conflict("Only closed decisions can reopen")
                mapping_state,review_state="needs_review","open"
            else:
                if item["mapping_state"] not in {"proposed","needs_review"} or item["review_state"]=="dismissed":raise Conflict("Item is closed; reopen first")
                states={"approve":("approved","resolved"),"choose":("approved","resolved"),"reject":("rejected","rejected"),"dismiss":("needs_review","dismissed")}
                if action not in states:raise ValueError("Unknown action")
                mapping_state,review_state=states[action]
            human={"action":action,"reason":reason.strip(),"timestamp":self._now(),"actor":"local-user",**(decision or {})}
            db.execute("UPDATE v4_items SET mapping_state=?,review_state=?,revision=revision+1,updated_at=?,human_decision=? WHERE id=? AND revision=?",(mapping_state,review_state,human["timestamp"],encode(human),id,revision))
            self._write_item_summary(db,self._decode(
                db.execute("SELECT * FROM v4_items WHERE id=?",(id,)).fetchone()))
            self._event(db,id,action,{"previous_revision":revision,"revision":revision+1,"decision":human})
            self._refresh_current_projections(db,{int(str(target["target_id"]).split(":",1)[0])
                for target in item["original"].get("targets",[])})
        return self.get_item(id)

    def activity(self,after=0,limit=50):
        with self._connect() as db:
            rows=db.execute("SELECT * FROM v4_events WHERE sequence>? ORDER BY sequence LIMIT ?",(after,limit)).fetchall()
        return [{**dict(r),"payload":json.loads(r["payload"])} for r in rows]

    def public_activity(self,actions,*,before=0,limit=50):
        """Return the public timeline with one indexed title join and no JSON decoding join."""
        actions=tuple(str(value) for value in actions)
        if not actions:return []
        placeholders=",".join("?" for _ in actions)
        with self._connect() as db:
            rows=db.execute(f"""SELECT e.*,t.title entity_title
                FROM v4_events e LEFT JOIN v4_item_titles t ON t.item_id=e.item_id
                WHERE e.action IN ({placeholders}) AND (?=0 OR e.sequence<?)
                ORDER BY e.sequence DESC LIMIT ?""",(*actions,before,before,limit)).fetchall()
        return [{**dict(row),"payload":json.loads(row["payload"])} for row in rows]

    @staticmethod
    def _override_row(row):
        urls=json.loads(row["source_urls"] or "[]")
        if not urls and row["source_url"]:urls=[row["source_url"]]
        return {"source_url":urls[0] if urls else None,"source_urls":urls,"audio":row["audio"],
            "ignore_errors":bool(row["ignore_errors"]),"manual_approved":bool(row["manual_approved"]),
            "excluded":bool(row["excluded"]),"episode_map":json.loads(row["episode_map"] or "[]"),
            "approval":json.loads(row["approval"]) if row["approval"] else None,
            "note":row["note"],"updated_at":row["updated_at"]}

    def season_overrides(self):
        with self._connect() as db:
            rows=db.execute("""SELECT target_id,source_url,source_urls,audio,ignore_errors,
                manual_approved,excluded,episode_map,approval,note,updated_at
                FROM v4_season_overrides ORDER BY target_id""").fetchall()
        return {row["target_id"]:self._override_row(row) for row in rows}

    def season_override(self,target_id):
        return self.season_overrides().get(target_id,{"source_url":None,"source_urls":[],"audio":None,
            "ignore_errors":False,"manual_approved":False,"excluded":False,"episode_map":[],
            "approval":None,"note":"","updated_at":None})

    def set_season_override(self,target_id,value):
        if not isinstance(target_id,str) or not target_id:raise ValueError("Invalid target id")
        now=self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM v4_current_targets WHERE target_id=?",(target_id,)).fetchone():
                raise Conflict("Target is not current")
            db.execute("""INSERT INTO v4_season_overrides(
                target_id,source_url,ignore_errors,note,updated_at,source_urls,audio,manual_approved,excluded,episode_map,approval)
                VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(target_id) DO UPDATE SET
                source_url=excluded.source_url,ignore_errors=excluded.ignore_errors,note=excluded.note,
                updated_at=excluded.updated_at,source_urls=excluded.source_urls,audio=excluded.audio,
                manual_approved=excluded.manual_approved,excluded=excluded.excluded,episode_map=excluded.episode_map,
                approval=excluded.approval""",
                (target_id,value["source_url"],1 if value["ignore_errors"] else 0,value["note"],now,
                 encode(value["source_urls"]),value["audio"],1 if value["manual_approved"] else 0,
                 1 if value["excluded"] else 0,encode(value["episode_map"]),
                 encode(value["approval"]) if value.get("approval") else None))
            self._event(db,None,"manual_override_applied",{"target_id":target_id,**value},"local-user")
            self._refresh_current_projections(db,{int(target_id.split(":",1)[0])})
        return self.season_override(target_id)

    def clear_season_override(self,target_id):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            deleted=db.execute("DELETE FROM v4_season_overrides WHERE target_id=?",(target_id,)).rowcount
            if deleted:
                self._event(db,None,"manual_override_cleared",{"target_id":target_id},"local-user")
                self._refresh_current_projections(db,{int(str(target_id).split(":",1)[0])})
        return {"target_id":target_id,"cleared":bool(deleted)}

    def notification_events(self,after=0,limit=50):
        with self._connect() as db:
            rows=db.execute("""SELECT sequence,audit_sequence,item_id,topic,timestamp,payload
                FROM v4_notification_events WHERE sequence>? ORDER BY sequence LIMIT ?""",(after,limit)).fetchall()
        return [{**dict(row),"payload":json.loads(row["payload"])} for row in rows]

    def notification_cursor(self,backend,initialize=False):
        if not isinstance(backend,str) or not backend:raise ValueError("Invalid notification backend")
        with self._connect() as db:
            row=db.execute("SELECT sequence FROM v4_notification_cursors WHERE backend=?",(backend,)).fetchone()
            if row:return int(row[0])
            sequence=0
            if initialize:
                latest=db.execute("SELECT COALESCE(MAX(sequence),0) FROM v4_notification_events").fetchone()
                sequence=int(latest[0] or 0)
            db.execute("INSERT INTO v4_notification_cursors VALUES (?,?,?)",(backend,sequence,self._now()))
            return sequence

    def advance_notification_cursor(self,backend,sequence):
        if not isinstance(sequence,int) or isinstance(sequence,bool) or sequence<0:raise ValueError("Invalid notification sequence")
        def operation(db):
            now=self._now()
            db.execute("""INSERT INTO v4_notification_cursors(backend,sequence,updated_at) VALUES (?,?,?)
                ON CONFLICT(backend) DO UPDATE SET
                sequence=MAX(v4_notification_cursors.sequence,excluded.sequence),updated_at=excluded.updated_at""",
                (backend,sequence,now))
            return int(db.execute("SELECT sequence FROM v4_notification_cursors WHERE backend=?",(backend,)).fetchone()[0])
        return self._idempotent_write("advance_notification_cursor",operation)

    def notification_delivery_state(self,backend,fingerprint):
        with self._connect() as db:
            row=db.execute("SELECT last_sent_at,last_sequence FROM v4_notification_delivery_state WHERE backend=? AND fingerprint=?",
                (backend,fingerprint)).fetchone()
        return dict(row) if row else None

    def record_notification_delivery(self,backend,fingerprint,sequence):
        now=self._now()
        def operation(db):
            db.execute("""INSERT INTO v4_notification_delivery_state(backend,fingerprint,last_sent_at,last_sequence)
                VALUES (?,?,?,?) ON CONFLICT(backend,fingerprint) DO UPDATE SET
                last_sent_at=excluded.last_sent_at,last_sequence=excluded.last_sequence""",
                (backend,fingerprint,now,sequence))
            db.execute("DELETE FROM v4_notification_retry_state WHERE backend=?",(backend,))
        self._idempotent_write("record_notification_delivery",operation)
        return now

    def notification_retry_state(self,backend):
        with self._connect() as db:
            row=db.execute("SELECT * FROM v4_notification_retry_state WHERE backend=?",(backend,)).fetchone()
        return dict(row) if row else None

    def record_notification_failure(self,backend,sequence,fingerprint,attempt_count,next_attempt_at,category):
        now=self._now();category=str(category)[:64]
        def operation(db):
            db.execute("""INSERT INTO v4_notification_retry_state(
                backend,sequence,fingerprint,attempt_count,next_attempt_at,last_attempt_at,last_error_category,updated_at)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(backend) DO UPDATE SET
                sequence=excluded.sequence,fingerprint=excluded.fingerprint,
                attempt_count=excluded.attempt_count,next_attempt_at=excluded.next_attempt_at,
                last_attempt_at=excluded.last_attempt_at,last_error_category=excluded.last_error_category,
                updated_at=excluded.updated_at""",
                (backend,sequence,fingerprint,attempt_count,next_attempt_at,now,category,now))
        self._idempotent_write("record_notification_failure",operation)

    def clear_notification_retry(self,backend):
        self._idempotent_write("clear_notification_retry",
            lambda db:db.execute("DELETE FROM v4_notification_retry_state WHERE backend=?",(backend,)))

    def notification_health(self,backend="telegram"):
        retry=self.notification_retry_state(backend)
        if not retry:return {"backend":backend,"status":"healthy","retrying":False}
        return {"backend":backend,"status":"degraded","retrying":True,
            "attempt_count":retry["attempt_count"],"next_attempt_at":retry["next_attempt_at"],
            "last_error_category":retry["last_error_category"]}

    def set_runtime_capability(self,capability):
        if not isinstance(capability,dict) or set(capability)!={"ready","status","diagnostics"}:
            raise ValueError("Invalid runtime capability")
        self._runtime_capability={"ready":bool(capability["ready"]),"status":str(capability["status"]),
            "diagnostics":[str(value)[:128] for value in capability["diagnostics"]]}

    def settings(self):
        defaults={"display_language":"it","notify_review":True,"notify_error":True,
            "notify_anomaly":True,"notify_manual_override":False,"notify_new_season":False,
            "auto_scan_enabled":False,"auto_scan_interval_minutes":60}
        with self._connect() as db:values={r[0]:json.loads(r[1]) for r in db.execute("SELECT key,value FROM v4_settings")}
        merged={**defaults,**{k:v for k,v in values.items() if k in defaults}}
        capability=dict(self._runtime_capability);effects=capability["ready"] is True
        event_preferences={
            "new_review":merged["notify_review"],"mapping_needs_attention":merged["notify_review"],
            "mapping_error":merged["notify_error"],"scan_error":merged["notify_error"],"download_error":merged["notify_error"],
            "unexpected_mapping_change":merged["notify_anomaly"],"mapping_conflict":merged["notify_anomaly"],
            "manual_override_applied":merged["notify_manual_override"],"manual_override_cleared":merged["notify_manual_override"],
            "new_season_detected":merged["notify_new_season"]}
        return {**merged,"mode":"production" if effects else "shadow","downloads_enabled":effects,"external_writes_enabled":effects,
            "llm_enabled":False,"notification_bus":"ready","notification_backends":supported_backends(),
            "notification_preferences":{"channel":"telegram","events":event_preferences},
            "telegram_status":"ready" if telegram_configured() else "not_configured",
            "download_capability":capability,"notification_health":self.notification_health()}

    def update_settings(self,values):
        allowed={"display_language","notify_review","notify_error","notify_anomaly","notify_manual_override","notify_new_season",
            "auto_scan_enabled","auto_scan_interval_minutes"}
        if not values or set(values)-allowed:raise ValueError("Unknown setting")
        if "display_language" in values and values["display_language"] not in {"it","en"}:raise ValueError("Invalid display language")
        for key in {"notify_review","notify_error","notify_anomaly","notify_manual_override","notify_new_season","auto_scan_enabled"}:
            if key in values and not isinstance(values[key],bool):raise ValueError("Toggle settings must be boolean")
        if "auto_scan_interval_minutes" in values:
            interval=values["auto_scan_interval_minutes"]
            if isinstance(interval,bool) or not isinstance(interval,int) or interval not in {15,30,60,180,360,720,1440}:
                raise ValueError("Unsupported automatic scan interval")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for key,value in values.items():
                db.execute("INSERT INTO v4_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,encode(value)))
            self._event(db,None,"settings_updated",values)
        return self.settings()
