"""
GMM API: XSLT mappings (`gmm_xslt_mapping`) + ISO8583 plugins (`gmm_iso8583_plugin`).
"""
import time

from flask import Blueprint, request, jsonify
import pymysql

from app.routes.transaction.rna_transactions import load_profiles
from app.utils.gmm_xslt_helper import (
    list_mappings as db_list_mappings,
    get_mapping as db_get_mapping,
    save_mapping as db_save_mapping,
    update_mapping as db_update_mapping,
    delete_mapping as db_delete_mapping,
    columns_meta as db_xslt_columns_meta,
    COLUMN_NAMES as XSLT_COLUMN_NAMES,
    list_history as db_list_xslt_history,
    get_history_entry as db_get_xslt_history_entry,
    delete_history_entry as db_delete_xslt_history_entry,
    clear_history as db_clear_xslt_history,
)
from app.utils.gmm_iso8583_helper import (
    list_plugins as db_list_iso8583_plugins,
    get_plugin as db_get_iso8583_plugin,
    create_plugin as db_create_iso8583_plugin,
    update_plugin as db_update_iso8583_plugin,
    delete_plugin as db_delete_iso8583_plugin,
)
from app.utils.gmm_data_definition_helper import (
    list_definitions as db_list_data_definitions,
    get_definition as db_get_data_definition,
    create_definition as db_create_data_definition,
    update_definition as db_update_data_definition,
    delete_definition as db_delete_data_definition,
)

gmm_bp = Blueprint("gmm", __name__)


def _get_db_profile(profile_id: str):
    profiles = load_profiles()
    return next((p for p in profiles if p.get("name") == profile_id), None)


def _connect_profile(profile: dict):
    return pymysql.connect(
        host=profile["host"],
        port=int(profile["port"]) if profile.get("port") else 3306,
        user=profile["user"],
        password=profile["password"],
        database=profile["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _resolve_profile_or_error(profile_id):
    if not profile_id:
        return None, (jsonify({"success": False, "error": "Missing required field: profileId"}), 400)
    profile = _get_db_profile(profile_id)
    if not profile:
        return None, (jsonify({"success": False, "error": f"Profile {profile_id} not found"}), 404)
    return profile, None


@gmm_bp.route("/xslt/list", methods=["POST"])
def api_list_xslt():
    """List rows from gmm_xslt_mapping. Body: { profileId, search? }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        mappings = db_list_mappings(conn, data.get("search") or "")
        conn.close()
        return jsonify(
            {
                "success": True,
                "data": {
                    "mappings": mappings,
                    "columns": XSLT_COLUMN_NAMES,
                    "columns_meta": db_xslt_columns_meta(),
                },
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/xslt/get", methods=["POST"])
def api_get_xslt():
    """Get one mapping including mappingdata. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    mapping_id = data.get("id")
    if mapping_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    try:
        conn = _connect_profile(profile)
        mapping = db_get_mapping(conn, int(mapping_id))
        conn.close()
        if not mapping:
            return jsonify({"success": False, "error": "Mapping not found"}), 404
        return jsonify({"success": True, "data": {"mapping": mapping}, "error": None})
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/xslt/save", methods=["POST"])
def api_save_xslt():
    """
    Save editor content to gmm_xslt_mapping.
    Body: {
      profileId, mode: 'overwrite'|'new', content,
      id?, name?, usercomment?
    }
    """
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    if data.get("content") is None:
        return jsonify({"success": False, "error": "Missing required field: content"}), 400
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        ok, payload = db_save_mapping(
            conn,
            mode=data.get("mode") or "",
            content=data.get("content"),
            usercomment=data.get("usercomment"),
            mapping_id=data.get("id"),
            name=data.get("name"),
        )
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 400
        return jsonify(
            {
                "success": True,
                "data": payload,
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/xslt/update", methods=["POST"])
def api_update_xslt():
    """
    Update a row in gmm_xslt_mapping.
    Body: { profileId, id, fields: { name?, usercomment?, mappingdata?/content? } }
    """
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    mapping_id = data.get("id")
    if mapping_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    fields = data.get("fields")
    if not isinstance(fields, dict):
        return jsonify({"success": False, "error": "Missing required field: fields (object)"}), 400
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        ok, payload = db_update_mapping(conn, int(mapping_id), fields)
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 400
        return jsonify(
            {
                "success": True,
                "data": payload,
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/xslt/delete", methods=["POST"])
def api_delete_xslt():
    """Delete a row from gmm_xslt_mapping. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    mapping_id = data.get("id")
    if mapping_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        ok, payload = db_delete_mapping(conn, int(mapping_id))
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 404
        return jsonify(
            {
                "success": True,
                "data": payload,
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/xslt/history", methods=["POST"])
def api_xslt_history():
    """List history for a mapping. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    mapping_id = data.get("id")
    if mapping_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    try:
        conn = _connect_profile(profile)
        history = db_list_xslt_history(conn, int(mapping_id))
        conn.close()
        return jsonify({"success": True, "data": {"history": history}, "error": None})
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/xslt/history/get", methods=["POST"])
def api_xslt_history_get():
    """Get one history entry content. Body: { profileId, history_id }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    history_id = data.get("history_id")
    if history_id is None:
        return jsonify({"success": False, "error": "Missing required field: history_id"}), 400
    try:
        conn = _connect_profile(profile)
        entry = db_get_xslt_history_entry(conn, int(history_id))
        conn.close()
        if not entry:
            return jsonify({"success": False, "error": "History entry not found"}), 404
        return jsonify({"success": True, "data": {"history": entry}, "error": None})
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/xslt/history/delete", methods=["POST"])
def api_xslt_history_delete():
    """Delete one history entry. Body: { profileId, history_id }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    history_id = data.get("history_id")
    if history_id is None:
        return jsonify({"success": False, "error": "Missing required field: history_id"}), 400
    try:
        conn = _connect_profile(profile)
        ok, payload = db_delete_xslt_history_entry(conn, int(history_id))
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 404
        return jsonify({"success": True, "data": payload, "error": None})
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/xslt/history/clear", methods=["POST"])
def api_xslt_history_clear():
    """Clear all history for a mapping. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    mapping_id = data.get("id")
    if mapping_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    try:
        conn = _connect_profile(profile)
        ok, payload = db_clear_xslt_history(conn, int(mapping_id))
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 400
        return jsonify({"success": True, "data": payload, "error": None})
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/iso8583/list", methods=["POST"])
def api_list_iso8583():
    """List rows from gmm_iso8583_plugin. Body: { profileId, search? }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        columns, plugins, columns_meta = db_list_iso8583_plugins(conn, data.get("search") or "")
        conn.close()
        return jsonify(
            {
                "success": True,
                "data": {
                    "columns": columns,
                    "columns_meta": columns_meta,
                    "plugins": plugins,
                },
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/iso8583/get", methods=["POST"])
def api_get_iso8583():
    """Get one row from gmm_iso8583_plugin. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    plugin_id = data.get("id")
    if plugin_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    try:
        conn = _connect_profile(profile)
        plugin = db_get_iso8583_plugin(conn, plugin_id)
        conn.close()
        if not plugin:
            return jsonify({"success": False, "error": "ISO8583 plugin not found"}), 404
        return jsonify({"success": True, "data": {"plugin": plugin}, "error": None})
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/iso8583/create", methods=["POST"])
def api_create_iso8583():
    """
    Insert a row into gmm_iso8583_plugin.
    Body: { profileId, fields: { col: value, ... } }
    Note: id is usually required (no AUTO_INCREMENT).
    """
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    fields = data.get("fields")
    if not isinstance(fields, dict):
        return jsonify({"success": False, "error": "Missing required field: fields (object)"}), 400
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        ok, payload = db_create_iso8583_plugin(conn, fields)
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 400
        return jsonify(
            {
                "success": True,
                "data": payload,
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/iso8583/update", methods=["POST"])
def api_update_iso8583():
    """
    Update a row in gmm_iso8583_plugin.
    Body: { profileId, id, fields: { col: value, ... } }
    """
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    plugin_id = data.get("id")
    if plugin_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    fields = data.get("fields")
    if not isinstance(fields, dict):
        return jsonify({"success": False, "error": "Missing required field: fields (object)"}), 400
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        ok, payload = db_update_iso8583_plugin(conn, plugin_id, fields)
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 400
        return jsonify(
            {
                "success": True,
                "data": payload,
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/iso8583/delete", methods=["POST"])
def api_delete_iso8583():
    """Delete a row from gmm_iso8583_plugin. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    plugin_id = data.get("id")
    if plugin_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        ok, payload = db_delete_iso8583_plugin(conn, plugin_id)
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 404
        return jsonify(
            {
                "success": True,
                "data": payload,
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/data-definition/list", methods=["POST"])
def api_list_data_definition():
    """List rows from gmm_data_definition. Body: { profileId, search? }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        columns, definitions, columns_meta = db_list_data_definitions(
            conn, data.get("search") or ""
        )
        conn.close()
        return jsonify(
            {
                "success": True,
                "data": {
                    "columns": columns,
                    "columns_meta": columns_meta,
                    "definitions": definitions,
                },
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/data-definition/get", methods=["POST"])
def api_get_data_definition():
    """Get one row from gmm_data_definition. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    definition_id = data.get("id")
    if definition_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    try:
        conn = _connect_profile(profile)
        definition = db_get_data_definition(conn, int(definition_id))
        conn.close()
        if not definition:
            return jsonify({"success": False, "error": "Data definition not found"}), 404
        return jsonify({"success": True, "data": {"definition": definition}, "error": None})
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/data-definition/create", methods=["POST"])
def api_create_data_definition():
    """
    Insert a row into gmm_data_definition.
    Body: { profileId, fields: { col: value, ... } }
    Note: id is usually required (no AUTO_INCREMENT). hash is computed server-side.
    """
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    fields = data.get("fields")
    if not isinstance(fields, dict):
        return jsonify({"success": False, "error": "Missing required field: fields (object)"}), 400
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        ok, payload = db_create_data_definition(conn, fields)
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 400
        return jsonify(
            {
                "success": True,
                "data": payload,
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/data-definition/update", methods=["POST"])
def api_update_data_definition():
    """
    Update a row in gmm_data_definition.
    Body: { profileId, id, fields: { col: value, ... } }
    """
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    definition_id = data.get("id")
    if definition_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    fields = data.get("fields")
    if not isinstance(fields, dict):
        return jsonify({"success": False, "error": "Missing required field: fields (object)"}), 400
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        ok, payload = db_update_data_definition(conn, int(definition_id), fields)
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 400
        return jsonify(
            {
                "success": True,
                "data": payload,
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500


@gmm_bp.route("/data-definition/delete", methods=["POST"])
def api_delete_data_definition():
    """Delete a row from gmm_data_definition. Body: { profileId, id }"""
    if not request.is_json:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400
    data = request.get_json() or {}
    profile, err = _resolve_profile_or_error(data.get("profileId"))
    if err:
        return err
    definition_id = data.get("id")
    if definition_id is None:
        return jsonify({"success": False, "error": "Missing required field: id"}), 400
    try:
        t0 = time.time()
        conn = _connect_profile(profile)
        ok, payload = db_delete_data_definition(conn, int(definition_id))
        conn.close()
        if not ok:
            return jsonify({"success": False, "error": payload.get("error")}), 404
        return jsonify(
            {
                "success": True,
                "data": payload,
                "execution_time": round(time.time() - t0, 6),
                "error": None,
            }
        )
    except Exception as e:
        import traceback

        return jsonify(
            {
                "success": False,
                "error": str(e),
                "details": traceback.format_exc(),
            }
        ), 500
