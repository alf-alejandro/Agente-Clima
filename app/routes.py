from flask import Blueprint, render_template, jsonify, request
from app.config import AI_COST_PER_CALL

bp = Blueprint("main", __name__)

# These are set by the app factory after creating the bot and portfolio
bot = None
portfolio = None


def init_routes(bot_instance, portfolio_instance):
    global bot, portfolio
    bot = bot_instance
    portfolio = portfolio_instance


@bp.route("/")
def dashboard():
    return render_template("dashboard.html")


@bp.route("/api/status")
def api_status():
    with portfolio.lock:
        snap = portfolio.snapshot()
    snap["bot_status"] = bot.status if bot else "unknown"
    snap["scan_count"] = bot.scan_count if bot else 0
    snap["last_opportunities"] = bot.last_opportunities if bot else []
    snap["ai_agent_enabled"] = bot.ai_agent_enabled if bot else False
    snap["ai_call_count"] = bot.ai_call_count if bot else 0
    snap["ai_cost_total"] = round(bot.ai_call_count * AI_COST_PER_CALL, 4) if bot else 0
    lpu = bot.last_price_update if bot else None
    snap["last_price_update"] = lpu.isoformat() if lpu else None
    snap["price_thread_alive"] = (
        bot._price_thread is not None and bot._price_thread.is_alive()
    ) if bot else False
    return jsonify(snap)


@bp.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    bot.start()
    return jsonify({"status": "running"})


@bp.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    bot.stop()
    return jsonify({"status": "stopped"})


@bp.route("/api/agent/toggle", methods=["POST"])
def api_agent_toggle():
    from app.config import GEMINI_API_KEY
    data = request.get_json(silent=True) or {}
    enable = data.get("enable", not bot.ai_agent_enabled)
    if enable:
        if not GEMINI_API_KEY:
            return jsonify({"error": "GEMINI_API_KEY no configurada en Railway"}), 400
        bot.enable_agent(GEMINI_API_KEY)
    else:
        bot.disable_agent()
    return jsonify({"ai_agent_enabled": bot.ai_agent_enabled})


@bp.route("/api/config", methods=["POST"])
def api_config():
    data = request.get_json(silent=True) or {}
    if "stop_loss_ratio" in data:
        ratio = float(data["stop_loss_ratio"])
        if not (0.1 <= ratio <= 3.0):
            return jsonify({"error": "stop_loss_ratio debe estar entre 0.1 y 3.0"}), 400
        with portfolio.lock:
            portfolio.stop_loss_ratio = round(ratio, 2)
    with portfolio.lock:
        return jsonify({"stop_loss_ratio": portfolio.stop_loss_ratio})
