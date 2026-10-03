import os
import sys
import threading
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram.warnings import PTBUserWarning

warnings.filterwarnings("ignore", category=PTBUserWarning)

from telegram import BotCommand
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

from app.bot.handlers import (
    cancel_game_callback,
    cancel_quiz_creation,
    coming_soon_callback,
    confirm_del_saved_question_callback,
    confirm_delete_question_callback,
    confirm_delete_quiz_callback,
    confirm_publish_callback,
    config_dest_callback,
    config_toggle_callback,
    continue_session_callback,
    creation_timer_callback,
    cycle_timer_callback,
    auto_shorten_options_callback,
    accept_shortened_option_callback,
    accept_all_shortened_callback,
    edit_shortened_option_callback,
    keep_orig_shortened_callback,
    auto_shorten_single_option_callback,
    delete_question_prompt_callback,
    delete_saved_question_prompt_callback,
    delete_saved_quiz_prompt_callback,
    discard_and_restart_callback,
    drop_invalid_questions_callback,
    edit_current_question_callback,
    edit_quiz_settings_callback,
    edit_saved_question_callback,
    execute_republish_callback,
    finish_creation_settings_callback,
    fix_invalid_question_callback,
    handle_poll_answer,
    help_command,
    join_game_callback,
    jump_next_invalid_callback,
    launch_selected_quiz_callback,
    multipart_done_callback,
    my_quizzes_command,
    my_quizzes_page_callback,
    myquizzes_noop_callback,
    preview_callback,
    preview_noop_callback,
    preview_saved_quiz_callback,
    prompt_channel_dest_callback,
    prompt_edit_explanation_callback,
    prompt_edit_option_callback,
    prompt_edit_question_text_callback,
    prompt_set_desc_callback,
    prompt_set_title_callback,
    receive_channel_dest_message,
    receive_question_edit_message,
    receive_quiz_desc_message,
    receive_quiz_part_message,
    receive_quiz_title_message,
    receive_republish_dest_message,
    replay_game_callback,
    republish_change_dest_callback,
    republish_confirm_callback,
    retry_failed_callback,
    set_correct_answer_callback,
    settings_callback,
    show_copyable_question_callback,
    skip_quiz_desc_callback,
    skip_quiz_title_callback,
    start_command,
    start_game_callback,
    start_publishing_callback,
    start_quiz_session_flow,
    startquiz_command,
    stopquiz_command,
    toggle_anonymous_callback,
    toggle_explanation_callback,
    toggle_header_banner_callback,
    toggle_shuffle_callback,
    view_quiz_callback,
)
from app.bot.states import QuizCreationState
from app.config.settings import get_settings
from app.database.database import init_db
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


async def post_init(application: Application) -> None:
    """Set bot commands for autocomplete in Telegram when typing '/'."""
    commands = [
        BotCommand("start", "Open main menu"),
        BotCommand("newquiz", "Create a new bulk quiz"),
        BotCommand("myquizzes", "View saved quizzes & 1-click re-publish"),
        BotCommand("startquiz", "Launch a live group quiz battle"),
        BotCommand("stopquiz", "Stop active group quiz battle"),
        BotCommand("bulk", "Multi-part bulk quiz creation"),
        BotCommand("help", "Formatting guide and instructions"),
        BotCommand("cancel", "Cancel current creation session"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Bot commands successfully registered with Telegram.")
    except Exception as e:
        logger.warning("Failed to register bot commands with Telegram: %s", e)


def create_bot_app() -> Application:
    """Build and configure the Telegram Bot Application with complete handlers."""
    settings = get_settings()

    # Ensure database tables exist
    try:
        init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.warning("Database init warning: %s", e)

    # Initialize Telegram Application with post_init hook for command registration
    application = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Bulk Quiz Creation Multi-Part Conversation Flow
    bulk_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_quiz_session_flow, pattern="^action_bulk_create$"),
            CommandHandler("newquiz", start_quiz_session_flow),
            CommandHandler("bulk", start_quiz_session_flow),
            CallbackQueryHandler(edit_saved_question_callback, pattern="^edit_saved_q_\\d+_\\d+$"),
        ],
        states={
            QuizCreationState.WAITING_FOR_TITLE: [
                CallbackQueryHandler(skip_quiz_title_callback, pattern="^skip_quiz_title$"),
                CommandHandler("skip", skip_quiz_title_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_quiz_title_message),
            ],
            QuizCreationState.WAITING_FOR_DESCRIPTION: [
                CallbackQueryHandler(skip_quiz_desc_callback, pattern="^skip_quiz_desc$"),
                CommandHandler("skip", skip_quiz_desc_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_quiz_desc_message),
            ],
            QuizCreationState.WAITING_FOR_BULK_INPUT: [
                CommandHandler("done", multipart_done_callback),
                CallbackQueryHandler(multipart_done_callback, pattern="^multipart_done$"),
                CallbackQueryHandler(auto_shorten_options_callback, pattern="^auto_shorten_options$"),
                CallbackQueryHandler(continue_session_callback, pattern="^multipart_continue$"),
                CallbackQueryHandler(discard_and_restart_callback, pattern="^multipart_discard$"),
                CallbackQueryHandler(fix_invalid_question_callback, pattern="^fix_invalid_q_\\d+$"),
                CallbackQueryHandler(drop_invalid_questions_callback, pattern="^drop_invalid_questions$"),
                CallbackQueryHandler(preview_callback, pattern="^goto_preview$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_quiz_part_message),
            ],
            QuizCreationState.CONFIGURING_SETTINGS: [
                CallbackQueryHandler(config_toggle_callback, pattern="^cfg_(timer|shuffle|anon|expl|banner)_.+$"),
                CallbackQueryHandler(config_dest_callback, pattern="^cfg_dest_.+$"),
                CallbackQueryHandler(finish_creation_settings_callback, pattern="^finish_creation_settings$"),
                CallbackQueryHandler(creation_timer_callback, pattern="^creation_timer_\\d+$"),
                CallbackQueryHandler(view_quiz_callback, pattern="^view_quiz_\\d+$"),
                CallbackQueryHandler(start_command, pattern="^action_main_menu$"),
                CallbackQueryHandler(prompt_set_title_callback, pattern="^prompt_set_title$"),
                CallbackQueryHandler(prompt_set_desc_callback, pattern="^prompt_set_desc$"),
                CallbackQueryHandler(toggle_header_banner_callback, pattern="^toggle_header_banner$"),
                CallbackQueryHandler(toggle_anonymous_callback, pattern="^toggle_anonymous$"),
                CallbackQueryHandler(toggle_shuffle_callback, pattern="^toggle_shuffle$"),
                CallbackQueryHandler(toggle_explanation_callback, pattern="^toggle_explanation$"),
                CallbackQueryHandler(cycle_timer_callback, pattern="^cycle_timer$"),
                CallbackQueryHandler(prompt_channel_dest_callback, pattern="^set_channel_dest$"),
                CallbackQueryHandler(settings_callback, pattern="^goto_settings$"),
                CallbackQueryHandler(preview_callback, pattern="^goto_preview$"),
                CallbackQueryHandler(confirm_publish_callback, pattern="^goto_confirm_publish$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_dest_message),
            ],
            QuizCreationState.PREVIEWING: [
                CallbackQueryHandler(preview_callback, pattern="^preview_nav_\\d+$"),
                CallbackQueryHandler(preview_noop_callback, pattern="^preview_noop$"),
                CallbackQueryHandler(auto_shorten_options_callback, pattern="^auto_shorten_options$"),
                CallbackQueryHandler(accept_shortened_option_callback, pattern="^accept_shortened_\\d+_\\d+$"),
                CallbackQueryHandler(accept_all_shortened_callback, pattern="^accept_all_shortened$"),
                CallbackQueryHandler(edit_shortened_option_callback, pattern="^edit_shortened_\\d+_\\d+$"),
                CallbackQueryHandler(keep_orig_shortened_callback, pattern="^keep_orig_shortened_\\d+_\\d+$"),
                CallbackQueryHandler(jump_next_invalid_callback, pattern="^jump_next_invalid$"),
                CallbackQueryHandler(fix_invalid_question_callback, pattern="^fix_invalid_q_\\d+$"),
                CallbackQueryHandler(drop_invalid_questions_callback, pattern="^drop_invalid_questions$"),
                CallbackQueryHandler(continue_session_callback, pattern="^multipart_continue$"),
                CallbackQueryHandler(edit_current_question_callback, pattern="^edit_current_question$"),
                CallbackQueryHandler(edit_saved_question_callback, pattern="^edit_saved_q_\\d+_\\d+$"),
                CallbackQueryHandler(delete_question_prompt_callback, pattern="^delete_current_question$"),
                CallbackQueryHandler(confirm_delete_question_callback, pattern="^confirm_delete_\\d+$"),
                CallbackQueryHandler(settings_callback, pattern="^goto_settings$"),
                CallbackQueryHandler(confirm_publish_callback, pattern="^goto_confirm_publish$"),
                CallbackQueryHandler(view_quiz_callback, pattern="^view_quiz_\\d+$"),
            ],
            QuizCreationState.EDITING_QUESTION: [
                CallbackQueryHandler(set_correct_answer_callback, pattern="^set_correct_ans_\\d+$"),
                CallbackQueryHandler(auto_shorten_single_option_callback, pattern="^auto_shorten_single_\\d+_\\d+$"),
                CallbackQueryHandler(prompt_edit_question_text_callback, pattern="^edit_q_text$"),
                CallbackQueryHandler(prompt_edit_option_callback, pattern="^edit_opt_\\d+$"),
                CallbackQueryHandler(prompt_edit_explanation_callback, pattern="^edit_q_expl$"),
                CallbackQueryHandler(show_copyable_question_callback, pattern="^show_copyable_q$"),
                CallbackQueryHandler(edit_current_question_callback, pattern="^edit_current_question$"),
                CallbackQueryHandler(preview_callback, pattern="^goto_preview$"),
                CallbackQueryHandler(view_quiz_callback, pattern="^view_quiz_\\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_question_edit_message),
            ],
            QuizCreationState.CONFIRMING_PUBLISH: [
                CallbackQueryHandler(start_publishing_callback, pattern="^confirm_publish_now$"),
                CallbackQueryHandler(retry_failed_callback, pattern="^retry_failed_quizzes$"),
                CallbackQueryHandler(preview_callback, pattern="^goto_preview$"),
                CallbackQueryHandler(settings_callback, pattern="^goto_settings$"),
            ],
        },
        fallbacks=[
            CommandHandler(["newquiz", "bulk"], start_quiz_session_flow),
            CallbackQueryHandler(cancel_quiz_creation, pattern="^action_cancel$"),
            CommandHandler("cancel", cancel_quiz_creation),
            CommandHandler("start", start_command),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )

    application.add_handler(bulk_conv_handler)

    # Standard command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("newquiz", start_quiz_session_flow))
    application.add_handler(CommandHandler("myquizzes", my_quizzes_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("startquiz", startquiz_command))
    application.add_handler(CommandHandler("stopquiz", stopquiz_command))

    # Live Group Quiz Battle callbacks
    application.add_handler(CallbackQueryHandler(join_game_callback, pattern="^game_join_\\d+$"))
    application.add_handler(CallbackQueryHandler(start_game_callback, pattern="^game_start_\\d+$"))
    application.add_handler(CallbackQueryHandler(cancel_game_callback, pattern="^game_cancel_\\d+$"))
    application.add_handler(CallbackQueryHandler(launch_selected_quiz_callback, pattern="^game_launch_\\d+$"))
    application.add_handler(CallbackQueryHandler(cancel_game_callback, pattern="^game_cancel_selection$"))
    application.add_handler(CallbackQueryHandler(replay_game_callback, pattern="^game_replay_\\d+$"))

    # Real-time poll answer tracking for live leaderboards
    application.add_handler(PollAnswerHandler(handle_poll_answer))

    # /myquizzes and saved quiz set callbacks
    application.add_handler(CallbackQueryHandler(my_quizzes_command, pattern="^action_saved_sets$"))
    application.add_handler(CallbackQueryHandler(my_quizzes_page_callback, pattern="^my_quizzes_list_\\d+$"))
    application.add_handler(CallbackQueryHandler(myquizzes_noop_callback, pattern="^myquizzes_noop$"))
    application.add_handler(CallbackQueryHandler(view_quiz_callback, pattern="^view_quiz_\\d+$"))
    application.add_handler(CallbackQueryHandler(preview_saved_quiz_callback, pattern="^preview_saved_\\d+_\\d+$"))
    application.add_handler(CallbackQueryHandler(republish_confirm_callback, pattern="^republish_confirm_\\d+$"))
    application.add_handler(CallbackQueryHandler(republish_change_dest_callback, pattern="^republish_change_dest_\\d+$"))
    application.add_handler(CallbackQueryHandler(execute_republish_callback, pattern="^execute_republish_\\d+$"))
    application.add_handler(CallbackQueryHandler(delete_saved_quiz_prompt_callback, pattern="^delete_saved_prompt_\\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_delete_quiz_callback, pattern="^confirm_delete_quiz_\\d+$"))
    application.add_handler(CallbackQueryHandler(delete_saved_question_prompt_callback, pattern="^del_saved_q_\\d+_\\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_del_saved_question_callback, pattern="^confirm_del_saved_q_\\d+_\\d+$"))
    application.add_handler(CallbackQueryHandler(edit_saved_question_callback, pattern="^edit_saved_q_\\d+_\\d+$"))
    application.add_handler(CallbackQueryHandler(creation_timer_callback, pattern="^creation_timer_\\d+$"))
    application.add_handler(CallbackQueryHandler(edit_quiz_settings_callback, pattern="^edit_quiz_settings_\\d+$"))
    application.add_handler(CallbackQueryHandler(config_toggle_callback, pattern="^cfg_(timer|shuffle|anon|expl|banner)_.+$"))
    application.add_handler(CallbackQueryHandler(config_dest_callback, pattern="^cfg_dest_.+$"))
    application.add_handler(CallbackQueryHandler(finish_creation_settings_callback, pattern="^finish_creation_settings$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_republish_dest_message))

    # General callback queries
    application.add_handler(CallbackQueryHandler(start_command, pattern="^action_main_menu$"))
    application.add_handler(CallbackQueryHandler(help_command, pattern="^action_help$"))
    application.add_handler(
        CallbackQueryHandler(
            coming_soon_callback,
            pattern="^(action_single_create|action_default_settings)$",
        )
    )

    async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log errors caused by updates and ignore harmless 'Message is not modified' errors."""
        if isinstance(context.error, BadRequest) and "message is not modified" in str(context.error).lower():
            logger.debug("Ignored harmless Telegram 'Message is not modified' error.")
            return
        logger.error("Exception while handling an update: %s", context.error, exc_info=context.error)

    application.add_error_handler(global_error_handler)

    return application


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler to satisfy Render/cloud health checks."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - BulkQuiz Bot is active and healthy")

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Suppress routine health check request logs to keep terminal logs clean
        pass


def start_health_check_server(port: int) -> None:
    """Start the lightweight health-check server in a background daemon thread."""
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info("Health-check server running on port %d for cloud hosting.", port)
        server.serve_forever()
    except Exception as e:
        logger.warning("Could not bind health-check server on port %d: %s", port, e)


def main() -> None:
    """Run the BulkQuiz bot."""
    settings = get_settings()
    logger.info("Starting BulkQuiz Bot in %s mode...", settings.ENVIRONMENT)

    # Start health-check server if PORT is provided by hosting environment (Render, etc.)
    port_env = os.environ.get("PORT")
    if port_env and port_env.isdigit():
        t = threading.Thread(target=start_health_check_server, args=(int(port_env),), daemon=True)
        t.start()

    if (
        not settings.TELEGRAM_BOT_TOKEN
        or settings.TELEGRAM_BOT_TOKEN in ("your_bot_token_here", "mock_token_for_tests")
    ):
        logger.error(
            "CRITICAL: Valid TELEGRAM_BOT_TOKEN is required to start live polling. "
            "Please configure TELEGRAM_BOT_TOKEN in .env or environment variables."
        )
        sys.exit(1)

    app = create_bot_app()
    logger.info("All BulkQuiz bot handlers successfully registered. Starting polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
