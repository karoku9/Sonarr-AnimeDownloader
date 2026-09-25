"""Notification classification and the optional Desktop LUL Telegram relay."""
from datetime import datetime,timezone,timedelta
import hashlib
import ipaddress
import json
import os
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit

_NOTIFICATION_TOPICS={
    # canonical semantic event names
    "new_review":"review",
    "mapping_needs_attention":"review",
    "mapping_error":"error",
    "scan_error":"error",
    "download_error":"error",
    "unexpected_mapping_change":"anomaly",
    "mapping_conflict":"anomaly",
    "manual_override_applied":"manual_override",
    "manual_override_cleared":"manual_override",
    "new_season_detected":"new_season",
    # existing lifecycle aliases kept for compatibility
    "review_created":"review",
    "execution_failed":"error",
    "execution_reconciliation_pending":"error",
    "scan_failed":"error",
    "runtime_error":"error",
    "provider_failed":"provider_error",
    "automatic_regression":"anomaly",
    "mapping_changed":"anomaly",
    "season_override_invalidated":"anomaly",
}
_ERROR_TOPICS={"error","provider_error"}
_ERROR_REPEAT_SECONDS=6*60*60
_TOPIC_SETTING={
    "review":"notify_review",
    "error":"notify_error",
    "provider_error":"notify_error",
    "anomaly":"notify_anomaly",
    "manual_override":"notify_manual_override",
    "new_season":"notify_new_season",
}


def notification_topic(action,payload=None):
    if not isinstance(action,str):return None
    return _NOTIFICATION_TOPICS.get(action)


def notification_fingerprint(event):
    """Stable identity for one error condition; volatile scan/job ids are ignored."""
    payload=event.get("payload") or {}
    targets=payload.get("target_ids") or ([payload.get("target_id")] if payload.get("target_id") else [])
    identity={
        "topic":event.get("topic"),
        "action":payload.get("action"),
        "code":payload.get("code") or payload.get("error_category"),
        "provider":payload.get("provider"),
        "item_id":event.get("item_id"),
        "targets":sorted(str(v) for v in targets if v is not None),
        "episode_id":payload.get("episode_id"),
        "episode":payload.get("episode") or payload.get("source_episode"),
        "source":payload.get("source_fingerprint") or payload.get("source_revision") or payload.get("source_url"),
        "stage":payload.get("stage") or payload.get("effect_stage"),
        "revision":payload.get("revision") or payload.get("mapping_revision") or payload.get("approved_revision"),
        "execution_key":payload.get("execution_key"),
    }
    raw=json.dumps(identity,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _within_repeat_window(event_timestamp,last_sent_at):
    try:
        current=datetime.fromisoformat(str(event_timestamp).replace("Z","+00:00"))
        previous=datetime.fromisoformat(str(last_sent_at).replace("Z","+00:00"))
        return 0 <= (current-previous).total_seconds() < _ERROR_REPEAT_SECONDS
    except (TypeError,ValueError):
        return False


def telegram_configured():
    try:return TelegramRelay().configured
    except ValueError:return False


def supported_backends():
    return [
        {"id":"outbox","status":"ready"},
        {"id":"telegram","status":"ready" if telegram_configured() else "not_configured"},
    ]


def telegram_error_text(event):
    payload=event.get("payload") or {}
    action=str(payload.get("action") or event.get("topic") or "error")
    topic=event.get("topic")
    labels={
        "new_review":"Review richiesta","mapping_needs_attention":"Mapping da verificare",
        "mapping_error":"Errore mapping","scan_error":"Scan fallito","download_error":"Download fallito",
        "unexpected_mapping_change":"Mapping cambiato","mapping_conflict":"Conflitto mapping",
        "manual_override_applied":"Override applicato","manual_override_cleared":"Override rimosso",
        "review_created":"Review richiesta",
        "scan_failed":"Scan fallito",
        "execution_failed":"Download/esecuzione fallita",
        "execution_reconciliation_pending":"Esecuzione da riconciliare",
        "runtime_error":"Errore runtime",
        "provider_failed":"Provider non disponibile",
        "automatic_regression":"Mapping regredito",
        "mapping_changed":"Mapping cambiato",
        "season_override_invalidated":"Override da verificare",
        "new_season_detected":"Nuova stagione rilevata",
    }
    icons={"review":"🟡","error":"🚨","provider_error":"🚨","anomaly":"⚠️","new_season":"🆕"}
    lines=[icons.get(topic,"ℹ️")+" AniDown V4 — "+labels.get(action,"Evento")]
    if payload.get("title"):lines.append(str(payload["title"])[:180])
    code=payload.get("code") or payload.get("error_category")
    if code:lines.append("Codice: "+str(code)[:160])
    targets=payload.get("target_ids") or ([payload["target_id"]] if payload.get("target_id") else None)
    if isinstance(targets,(list,tuple)) and targets:lines.append("Target: "+", ".join(map(str,targets[:8])))
    lines.append("Desktop LUL")
    return "\n".join(lines)[:3500]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


def _validated_relay_url(value,has_credential):
    if not value:return ""
    parsed=urlsplit(value)
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        raise ValueError("Invalid Telegram relay URL")
    if parsed.scheme not in {"http","https"}:
        raise ValueError("Telegram relay must use HTTPS or loopback HTTP")
    loopback=parsed.hostname.casefold()=="localhost"
    try:loopback=loopback or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:pass
    if has_credential and parsed.scheme!="https" and not loopback:
        raise ValueError("Credentialed Telegram relay requires HTTPS or loopback transport")
    return value


class TelegramRelay:
    def __init__(self,url=None,key=None,timeout=6,opener=None):
        self.url=(url or os.getenv("ANIDOWN_V4_TELEGRAM_RELAY_URL") or "").strip()
        self.key=(key or os.getenv("ANIDOWN_V4_TELEGRAM_RELAY_KEY") or os.getenv("AGENT_KEY") or "").strip()
        self.url=_validated_relay_url(self.url,bool(self.key))
        self.timeout=timeout
        self.opener=opener or urllib.request.build_opener(_NoRedirect())

    @property
    def configured(self):return bool(self.url and self.key)

    def send(self,event):
        if not self.configured:raise RuntimeError("telegram relay is not configured")
        raw=json.dumps({"text":telegram_error_text(event),"silent":False}).encode("utf-8")
        request=urllib.request.Request(self.url,data=raw,method="POST",headers={
            "Content-Type":"application/json","X-Agent-Key":self.key})
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                if not 200<=response.status<300:raise RuntimeError("telegram_relay_rejected")
        except RuntimeError:raise
        except urllib.error.HTTPError:raise RuntimeError("telegram_relay_rejected") from None
        except Exception:raise RuntimeError("telegram_relay_unavailable") from None


class NotificationWorker:
    def __init__(self,store,relay=None,poll_seconds=1.5,clock=None):
        self.store=store;self.relay=relay or TelegramRelay();self.poll_seconds=poll_seconds
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.stop_event=threading.Event();self.thread=None;self.cursor=0

    def start(self):
        if not self.relay.configured:return False
        self.cursor=self.store.notification_cursor("telegram",initialize=True)
        self.thread=threading.Thread(target=self._run,name="anidown-v4-telegram",daemon=True)
        self.thread.start();return True

    def _run(self):
        while True:
            wait_seconds=self.poll_seconds
            retry=self.store.notification_retry_state("telegram")
            if retry:
                try:
                    due=datetime.fromisoformat(retry["next_attempt_at"].replace("Z","+00:00"))
                    wait_seconds=max(wait_seconds,min(3600,max(0,(due-self.clock()).total_seconds())))
                except (TypeError,ValueError):pass
            if self.stop_event.wait(wait_seconds):break
            self.run_once()

    @staticmethod
    def _failure_category(error):
        value=str(error)
        if value in {"telegram_relay_rejected","telegram_relay_unavailable"}:return value
        return "telegram_relay_unavailable"

    def _next_attempt(self,fingerprint,attempt):
        base=min(3600,15*(2**min(attempt-1,8)))
        jitter=int(hashlib.sha256(f"{fingerprint}:{attempt}".encode()).hexdigest()[:8],16)%max(1,base//4+1)
        return self.clock()+timedelta(seconds=min(3600,base+jitter))

    def run_once(self):
        retry=self.store.notification_retry_state("telegram")
        if retry:
            try:due=datetime.fromisoformat(retry["next_attempt_at"].replace("Z","+00:00"))
            except (TypeError,ValueError):due=self.clock()
            if due>self.clock():return 0
        events=self.store.notification_events(after=self.cursor,limit=20)
        delivered=0
        for event in events:
                topic=event.get("topic")
                setting=_TOPIC_SETTING.get(topic)
                enabled=bool(setting and self.store.settings().get(setting,False))
                if enabled:
                    payload=dict(event.get("payload") or {})
                    item_id=event.get("item_id")
                    if item_id:
                        try:
                            item=self.store.get_item(item_id)
                            targets=item["original"].get("targets",[])
                            if targets:
                                payload.setdefault("title",targets[0].get("canonical_title"))
                                payload.setdefault("target_ids",[t.get("target_id") for t in targets if t.get("target_id")])
                        except Exception:pass
                    enriched={**event,"payload":payload}
                    fingerprint=None;suppressed=False
                    if topic in _ERROR_TOPICS:
                        fingerprint=notification_fingerprint(enriched)
                        state=self.store.notification_delivery_state("telegram",fingerprint)
                        suppressed=bool(state and _within_repeat_window(event.get("timestamp"),state.get("last_sent_at")))
                    if not suppressed:
                        try:
                            self.relay.send(enriched)
                            if fingerprint:self.store.record_notification_delivery("telegram",fingerprint,event["sequence"])
                            else:self.store.clear_notification_retry("telegram")
                            delivered+=1
                        except Exception as error:
                            prior=self.store.notification_retry_state("telegram")
                            attempt=(int(prior["attempt_count"])+1) if prior and prior["sequence"]==event["sequence"] else 1
                            retry_fingerprint=fingerprint or hashlib.sha256(f"event:{event['sequence']}".encode()).hexdigest()
                            due=self._next_attempt(retry_fingerprint,attempt).isoformat()
                            self.store.record_notification_failure("telegram",event["sequence"],retry_fingerprint,
                                attempt,due,self._failure_category(error))
                            break
                self.store.advance_notification_cursor("telegram",event["sequence"])
                self.cursor=event["sequence"]
                self.store.clear_notification_retry("telegram")
        return delivered

    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=max(2,self.poll_seconds+1))
