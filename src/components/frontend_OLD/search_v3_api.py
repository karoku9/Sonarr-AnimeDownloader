import json

from flask import Response, request


def response_json(data, status=200):
	return Response(
		mimetype="application/json",
		status=status,
		response=json.dumps(data),
		headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"},
	)


def loadSearchV3API(app, core):
	@app.route("/api/search-v3/debug", methods=["GET"])
	def search_v3_debug():
		query = (request.args.get("query") or "").strip()
		if not query:
			return response_json({"search_engine": "v3", "status": "provider_error", "errors": ["query is required."]}, 400)
		result = core.search_v3.search(
			query,
			language_preference=request.args.get("language_preference", "AUTO"),
			debug=True,
			refresh_index=request.args.get("refresh") == "1",
			fetch_external_aliases=request.args.get("external_aliases") == "1",
			auto_refresh=False,
		)
		return response_json(result)

	@app.route("/api/search-v3/search", methods=["POST"])
	def search_v3_search():
		data = request.json or {}
		title = (data.get("title") or "").strip()
		if not title:
			return response_json({"search_engine": "v3", "status": "provider_error", "errors": ["title is required."]}, 400)
		series = data.get("series") or {
			key: data[key] for key in ("title", "cleanTitle", "sortTitle", "originalTitle", "alternateTitles") if key in data
		}
		result = core.mapping_automation.search_mapping(
			series,
			data.get("season", 1),
			language_preference=data.get("language_preference", "AUTO"),
			force_rematch=bool(data.get("force_rematch", False)),
			debug=bool(data.get("debug", False)),
			refresh_index=bool(data.get("refresh_index", False)),
			fetch_external_aliases=bool(data.get("external_aliases", False)),
			auto_refresh=True,
		)
		return response_json(result)

	@app.route("/api/search-v3/index-status", methods=["GET"])
	def search_v3_index_status():
		return response_json({"search_engine": "v3", "index": core.search_v3.index_status()})

	@app.route("/api/search-v3/refresh-index", methods=["POST"])
	def search_v3_refresh_index():
		try:
			return response_json(core.search_v3.refresh_index())
		except Exception as error:
			return response_json({"search_engine": "v3", "status": "provider_error", "errors": [str(error)]}, 502)

	@app.route("/api/search-v3/aliases", methods=["POST"])
	def search_v3_aliases():
		data = request.json or {}
		title = (data.get("title") or "").strip()
		if not title:
			return response_json({"search_engine": "v3", "status": "provider_error", "errors": ["title is required."]}, 400)
		aliases = core.search_v3.alias_provider.set_manual_aliases(title, data.get("aliases", []))
		return response_json({"search_engine": "v3", "title": title, "manual_aliases": aliases, "status": "saved", "errors": []})
