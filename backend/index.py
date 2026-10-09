"""Vercel entry point for the StockFlow FastAPI application."""

from app.main import create_app

app = create_app()