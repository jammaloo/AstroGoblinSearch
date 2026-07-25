#!/usr/bin/env python3
"""CLI entry point for the web server."""
from app import config, web

if __name__ == "__main__":
    web.app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False)
