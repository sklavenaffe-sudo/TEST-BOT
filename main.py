from fastapi import FastAPI
import os

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Clothoff Webhook Bot is alive! 🚀 Use /webhook for Clothoff callbacks."}

@app.get("/health")
async def health():
    return {"status": "healthy"}

# Для webhook (пока заглушка)
@app.post("/webhook")
async def clothoff_webhook():
    return {"status": "received"}  # Clothoff требует 200 OK