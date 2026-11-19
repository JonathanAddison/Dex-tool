def register(reg):
    def hello(a):
        return f"سلام {a.get('name','دنیا')}!"
    schema = {
        "type": "function",
        "function": {
            "name": "plugin_hello",
            "description": "سلام کردن از طرف پلاگین.",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": []}
        }
    }
    reg.register_tool(schema, hello, category="plugin", base=False)
