"""OpenAI client cache construction, kept separate from request handling."""


_client_cache = {}

def get_openai_client(api_key, base_url, proxy, openai_sdk, cache, cache_getter):
    cache_key = (api_key, base_url, proxy)
    clients = cache_getter('openai_client', cache)
    if cache_key not in clients:
        client_args = {"api_key": api_key, "base_url": base_url}
        if proxy:
            from httpx import AsyncClient
            client_args["http_client"] = AsyncClient(proxies={
                "all://*openai.com": f"http://{proxy}"
            })
        clients[cache_key] = openai_sdk.AsyncOpenAI(**client_args)
    return clients[cache_key]
