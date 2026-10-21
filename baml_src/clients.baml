// LLM clients. Swap the model here; every function picks it up.
// Haiku 5.5 thinks by default and thinking counts toward max_tokens, so leave headroom;
// low effort is plenty for labeling one row.
client Haiku = ai.clients.Retry.new(
    inner = anthropic.AnthropicClient.new(
        model = "claude-haiku-5-5",
        api_key = env.ANTHROPIC_API_KEY,
        max_tokens = 4096,
        request_body = effort("low"),
    ),
    max_attempts = 3,
);

// Extra request-body fields: {"output_config": {"effort": level}}
function effort(level: string) -> baml.json.json {
    let config: map<string, baml.json.json> = { "effort": level };
    let body: map<string, baml.json.json> = { "output_config": config };
    body
}

// Haiku with Anthropic's server-side web search tool, for looking things up (same setup as
// ../loitering). Each search is billed per use; max_uses caps it per call.
client HaikuSearch = ai.clients.Retry.new(
    inner = anthropic.AnthropicClient.new(
        model = "claude-haiku-5-5",
        api_key = env.ANTHROPIC_API_KEY,
        max_tokens = 4096,
        request_body = web_search(3),
    ),
    max_attempts = 3,
);

// Extra request-body fields: low effort plus the web search server tool
function web_search(max_uses: int) -> baml.json.json {
    let tool: map<string, baml.json.json> = { "type": "web_search_20250305", "name": "web_search", "max_uses": max_uses };
    let tools: baml.json.json[] = [tool];
    let config: map<string, baml.json.json> = { "effort": "low" };
    let body: map<string, baml.json.json> = { "tools": tools, "output_config": config };
    body
}

// One search per call: URL discovery (Addendum 2 #8) runs once per row.
client HaikuSearchOnce = ai.clients.Retry.new(
    inner = anthropic.AnthropicClient.new(
        model = "claude-haiku-5-5",
        api_key = env.ANTHROPIC_API_KEY,
        max_tokens = 4096,
        request_body = web_search(1),
    ),
    max_attempts = 3,
);
