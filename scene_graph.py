import datetime
import json
import re
import difflib
from collections import defaultdict
from openai import OpenAI

class LocationNode:
    def __init__(self, name):
        self.name = name
        self.children = {}
        self.items = set()
        self.orientation_nodes = {}

class SceneGraph:
    def __init__(self):
        self.root = {}
        self.item_index = {}
        self.history = defaultdict(list)
        self.dialog_history = []
        self.location_relation = {}
        self._init_known_locations()

    def _init_known_locations(self):
        for path in [["办公室A"], ["卧室"], ["书房", "桌子"], ["客厅"]]:
            self._get_or_create_path(path)

    def _get_or_create_path(self, location_path):
        current_level = self.root
        for i, loc in enumerate(location_path):
            if loc not in current_level:
                current_level[loc] = LocationNode(loc)
            current_level = current_level[loc].children
            if i > 0:
                self.location_relation[location_path[i]] = location_path[:i]

    def _get_node(self, location_path):
        current = self.root
        node = None
        for loc in location_path:
            if loc in current:
                node = current[loc]
                current = node.children
            else:
                return None
        return node

    def add_or_update_item(self, item, location_path, orientation=None):
        now = datetime.datetime.now().isoformat()
        old_path = self.item_index.get(item)

        # 清除旧位置（无论是否带 orientation）
        if old_path:
            old_node_path = old_path[:-1] if old_path[-1] in ["上", "下", "里", "外", "中"] else old_path
            old_node = self._get_node(old_node_path)
            if old_node:
                if len(old_path) > len(old_node_path):  # 有 orientation
                    old_orient = old_path[-1]
                    if old_orient in old_node.orientation_nodes:
                        old_node.orientation_nodes[old_orient].discard(item)
                        if not old_node.orientation_nodes[old_orient]:
                            del old_node.orientation_nodes[old_orient]
                else:
                    old_node.items.discard(item)

        # 获取或创建目标位置
        current = self.root
        for loc in location_path:
            if loc not in current:
                current[loc] = LocationNode(loc)
            node = current[loc]
            current = node.children

        # 添加新位置
        if orientation:
            if orientation not in node.orientation_nodes:
                node.orientation_nodes[orientation] = set()
            node.orientation_nodes[orientation].add(item)
            self.item_index[item] = location_path + [orientation]
        else:
            node.items.add(item)
            self.item_index[item] = location_path

        # 历史记录
        self.history[item].append((self.item_index[item].copy(), now))
        self.dialog_history.append(f"[{now}] 添加/更新：{item} -> {'/'.join(self.item_index[item])}")

        # 自动补充容器位置
        if len(location_path) >= 2:
            container = location_path[-1]
            container_path = location_path[:-1]
            if container not in self.item_index:
                self.item_index[container] = container_path
                self.dialog_history.append(f"[{now}] 自动记录容器：{container} -> {'/'.join(container_path)}")


    def merge_path_relation(self, child, parent_path):
        """将根下 child 合并至 parent_path + [child]，并迁移其下所有 item、子节点、orientation"""
        shallow_path = [child]
        deep_path = parent_path + [child]

        # 记录 child 属于哪个上层路径
        self.location_relation[child] = parent_path

        shallow_node = self._get_node(shallow_path)
        if not shallow_node:
            return 

        # 获取或创建目标节点
        self._get_or_create_path(deep_path)
        deep_node = self._get_node(deep_path)

        # 合并普通物品
        for item in shallow_node.items:
            deep_node.items.add(item)
            self.item_index[item] = deep_path
            now = datetime.datetime.now().isoformat()
            self.history[item].append((deep_path.copy(), now))
        shallow_node.items.clear()

        # 合并 orientation 下的物品
        for orient, items in shallow_node.orientation_nodes.items():
            if orient not in deep_node.orientation_nodes:
                deep_node.orientation_nodes[orient] = set()
            for item in items:
                deep_node.orientation_nodes[orient].add(item)
                self.item_index[item] = deep_path + [orient]
                now = datetime.datetime.now().isoformat()
                self.history[item].append((deep_path.copy() + [orient], now))
            items.clear()
        shallow_node.orientation_nodes.clear()

        # 合并子节点
        for cname, cnode in shallow_node.children.items():
            if cname not in deep_node.children:
                deep_node.children[cname] = cnode
            else:
                pass
        shallow_node.children.clear()

        # 移除原来的 child 节点（如果在根下）
        if child in self.root:
            del self.root[child]

    def query_item(self, item_name):
        normalized = re.sub(r"(我的|这[个本只]?|那个|一[个本只]?)", "", item_name).strip()
        if normalized in self.item_index:
            path = self.item_index[normalized]
            return f"{item_name} 当前在：{'/'.join(path)}"
        matches = difflib.get_close_matches(normalized, self.item_index.keys(), n=1, cutoff=0.5)
        if matches:
            m = matches[0]
            return f"没有找到“{item_name}”，是否想查“{m}”？在：{'/'.join(self.item_index[m])}"
        return f"没有找到物品：{item_name}"

    def history_of(self, item_name):
        if item_name not in self.history:
            return f"{item_name} 没有历史记录"
        records = self.history[item_name]
        return "\n".join([f"{'->'.join(loc)} @ {t}" for loc, t in records])

    def structure(self):
        def walk(tree, indent=""):
            lines = []
            for k, node in tree.items():
                lines.append(indent + f"- {k}")
                for orient, items in node.orientation_nodes.items():
                    lines.append(indent + f"  ◾ {orient}：")
                    for item in items:
                        lines.append(indent + f"    📦 {item}")
                for item in node.items:
                    lines.append(indent + f"  📦 {item}")
                lines.extend(walk(node.children, indent + "  "))
            return lines
        return "\n".join(walk(self.root))
    
    def get_scene_info(self):
        """获取当前场景的物品及位置结构（简明语义视图）"""
        lines = []

        def dfs(node_dict, path=""):
            for name, node in node_dict.items():
                current_path = f"{path}/{name}" if path else name
                for orientation, items in node.orientation_nodes.items():
                    for item in items:
                        lines.append(f"{current_path}【{orientation}】有：{item}")
                for item in node.items:
                    lines.append(f"{current_path} 有：{item}")
                dfs(node.children, current_path)

        dfs(self.root)

        if not lines:
            return "当前场景中没有记录任何物品。"
        return "\n".join(lines)

class DeepSeekParser:
    def __init__(self, api_key, sg):
        self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self.sg = sg
        self.dialog_history = [{
            "role": "system",
            "content": (
                "你是一个语言理解助手，用户会用自然语言表达物品的放置、位置之间的包含关系，或者想查询物品位置。\n"
                "用户的表达可能不规范或很口语化，你要尽量理解并提取出结构化信息。\n"
                "你需要识别用户的意图（type），类型包括：\n"
                "- \"add\": 表示添加物品位置信息（如“我把钥匙放在桌子上了”）\n"
                "- \"merge\": 表示位置之间的包含关系（如“书桌在卧室里”）\n"
                "- \"query\": 表示用户想查物品在哪（如“我手机在哪”）\n\n"
                "- \"scene_info\": 表示用户想查看当前记录了哪些物品或位置信息（如“你都记录了什么”“现在有哪些东西”）\n"

                "解析后的JSON输出格式如下：\n"
                "{\n"
                "  \"type\": \"add\", \n"
                "  \"item\": \"钥匙\", \n"
                "  \"location_path\": [\"卧室\", \"桌子\"],\n"
                "  \"orientation\": \"上\"\n"
                "}\n"
                "或：\n"
                "{\n"
                "  \"type\": \"merge\",\n"
                "  \"location_child\": \"桌子\",\n"
                "  \"location_path\": [\"卧室\"]\n"
                "}\n"
                "或：\n"
                "{\n"
                "  \"type\": \"query\",\n"
                "  \"item\": \"手机\"\n"
                "}\n"
                "或： \n"
                "{\n"
                "  \"type\": \"scene_info\"\n"
                "}"
            )
        }]

    def parse(self, text):
        self.dialog_history.append({"role": "user", "content": text})
        res = self.client.chat.completions.create(
            model="deepseek-chat",
            messages=self.dialog_history,
            temperature=0.2,
            max_tokens=500
        )
        msg = res.choices[0].message.content.strip()
        self.dialog_history.append({"role": "assistant", "content": msg})

        # 清理 JSON 响应
        cleaned = re.sub(r"^```json|```$", "", msg.strip(), flags=re.MULTILINE).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # 返回未知类型兜底
            return {"type": "unknown", "text": text}

        # 处理结构
        intent = data.get("type")

        if intent == "add":
            if "item" in data and "location_path" in data:
                self.sg.add_or_update_item(
                    data["item"],
                    data["location_path"],
                    orientation=data.get("orientation")
                )
            else:
                return {"type": "unknown", "text": text}

        elif intent == "merge":
            if "location_child" in data and "location_path" in data:
                self.sg.merge_path_relation(data["location_child"], data["location_path"])
            else:
                return {"type": "unknown", "text": text}

        elif intent == "query":
            if "item" in data:
                return data
            else:
                return {"type": "unknown", "text": text}
            
        elif intent == "scene_info":
            return {
                "type": "scene_info",
                "info": self.sg.get_scene_info()
            }
        
        else:
            return {"type": "unknown", "text": text}

        return data
