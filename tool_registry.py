import json, os, importlib.util

class ToolRegistry:
    def __init__(self, repo_root, max_native=16):
        self.repo_root = repo_root
        self.max_native = max_native
        self.tools = {}       # name -> {schema, func, category, skill}
        self.skills = {}      # name -> {description, tools, prompt, path}
        self.plugins = {}     # name -> {path, meta, tools}
        self.active_skill = None
        self.base_tools = set()

    def register_tool(self, schema, func, category="general", skill=None, base=False):
        name = schema["function"]["name"]
        self.tools[name] = {"schema": schema, "func": func,
                           "category": category, "skill": skill}
        if base:
            self.base_tools.add(name)

    # ---------- Skills ----------
    def load_skill(self, skill_dir):
        path = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(path):
            return
        content = open(path, encoding="utf-8").read()
        meta, body = {}, content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
                body = parts[2].strip()
        name = meta.get("name", os.path.basename(skill_dir))
        tools = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
        self.skills[name] = {
            "description": meta.get("description", ""),
            "tools": tools,
            "prompt": body,
            "path": path,
        }

    def load_all_skills(self, base_dir):
        if not os.path.isdir(base_dir):
            return
        for entry in sorted(os.listdir(base_dir)):
            full = os.path.join(base_dir, entry)
            if os.path.isdir(full):
                self.load_skill(full)

    def activate_skill(self, name):
        if name not in self.skills:
            return None
        self.active_skill = name
        return self.skills[name]

    def deactivate_skill(self):
        self.active_skill = None

    # ---------- Plugins ----------
    def load_plugin(self, plugin_dir):
        manifest = os.path.join(plugin_dir, ".claude-plugin", "plugin.json")
        if not os.path.isfile(manifest):
            manifest = os.path.join(plugin_dir, "plugin.json")
        if not os.path.isfile(manifest):
            return
        meta = json.load(open(manifest, encoding="utf-8"))
        name = meta.get("name", os.path.basename(plugin_dir))
        py = os.path.join(plugin_dir, "plugin.py")
        if not os.path.isfile(py):
            return
        spec = importlib.util.spec_from_file_location(name, py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "register"):
            mod.register(self)
        self.plugins[name] = {"path": plugin_dir, "meta": meta}

    def load_all_plugins(self, base_dir):
        if not os.path.isdir(base_dir):
            return
        for entry in sorted(os.listdir(base_dir)):
            full = os.path.join(base_dir, entry)
            if os.path.isdir(full):
                self.load_plugin(full)

    # ---------- Native Schemas ----------
    def get_active_schemas(self):
        base = [self.tools[n]["schema"] for n in self.base_tools if n in self.tools]
        if self.active_skill and self.active_skill in self.skills:
            sk = self.skills[self.active_skill]
            extra = [self.tools[t]["schema"] for t in sk["tools"] if t in self.tools]
            seen, combined = set(), []
            for s in extra + base:
                n = s["function"]["name"]
                if n not in seen:
                    seen.add(n); combined.append(s)
            return combined[:self.max_native]
        return base[:self.max_native]

    def get_skill_prompt(self):
        if self.active_skill and self.active_skill in self.skills:
            return self.skills[self.active_skill]["prompt"]
        return None

    # ---------- Execute ----------
    def execute(self, name, args):
        if name not in self.tools:
            return f"ابزار ناشناخته: {name}"
        try:
            return self.tools[name]["func"](args)
        except Exception as e:
            return f"خطا در {name}: {e}"
