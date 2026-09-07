"""Cross-cutting user activity registration for VideoBot-Next."""

from telegram.ext import MessageHandler, filters


def register_user_activity(app, bot_module):
    """Register a non-blocking user activity middleware handler."""
    async def track_user_activity(update, context):
        user = update.effective_user
        if user is not None:
            bot_module.register_user(user)

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, track_user_activity),
        group=-100,
    )
