from flask import Blueprint, render_template, jsonify

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
    return jsonify(snap)


@bp.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    bot.start()
    return jsonify({"status": "running"})


@bp.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    bot.stop()
    return jsonify({"status": "stopped"})
