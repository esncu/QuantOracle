from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI()


@app.get("/test/{tag}")
def read_item(tag: str, query: str | None = None):
    if tag == "hi":
        return {"message": "it works! try another tag.", "query": query}
    else:
        return {"message": "it works, and you changed the tag!", "query": query}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
