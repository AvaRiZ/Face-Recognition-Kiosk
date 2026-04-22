from core.config import AppConfig
from core.state import AppStateManager
from database.repository import UserRepository
from auth import init_auth_db
from routes.routes import init_imported_logs_table
from app.flask_app import create_flask_app
from app.realtime import socketio


def main():
    print("boot: config", flush=True)
    config = AppConfig()
    print("boot: repository", flush=True)
    repository = UserRepository(config.db_path)
    repository.init_db()
    print("boot: auth", flush=True)
    init_auth_db()
    init_imported_logs_table(config.db_path)

    print("boot: state", flush=True)
    state = AppStateManager(config)
    state.load_users(repository.get_all_users())

    print("boot: flask app", flush=True)
    app = create_flask_app(config, state, repository, None)
    print("boot: socketio.run", flush=True)
    socketio.run(
        app,
        host="127.0.0.1",
        port=5000,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    main()
