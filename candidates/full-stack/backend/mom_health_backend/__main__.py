"""Run only after explicit runtime approval. No service starts on import."""
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("mom_health_backend.app:create_app", factory=True, host="127.0.0.1", port=8765, proxy_headers=False, access_log=False)
