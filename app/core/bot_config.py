import os
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
from app.core.config import settings
from app.core.logger import logger

class BotConfig:
    APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
    APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")
    APP_TENANT_ID = os.environ.get("MICROSOFT_APP_TENANT_ID", "")

adapter_settings = BotFrameworkAdapterSettings(
    app_id=BotConfig.APP_ID, 
    app_password=BotConfig.APP_PASSWORD,
    channel_auth_tenant=BotConfig.APP_TENANT_ID if BotConfig.APP_TENANT_ID else None
)
bot_adapter = BotFrameworkAdapter(adapter_settings)

# Optional: Add error handler
async def on_error(context, error):
    logger.error(f"[BotFramework] Unhandled error: {error}")
    await context.send_activity("The bot encountered an error or bug.")

bot_adapter.on_turn_error = on_error
