"""`python -m orq` arranca el servidor (por defecto local: http://127.0.0.1:8000)."""
from .api.app import HOST, PORT, main

if __name__ == "__main__":
    print(f"Simulador en http://{HOST}:{PORT}  (API docs: /docs)  — Ctrl+C para salir", flush=True)
    main()
