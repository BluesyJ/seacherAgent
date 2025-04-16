from flask import Flask, render_template, request, jsonify
from scene_graph import SceneGraph, DeepSeekParser

import os
import subprocess
from funasr import AutoModel
from funasr.utils.postprocess_utils import rich_transcription_postprocess

app = Flask(__name__)

# 🧠 初始化模块
API_KEY = "sk-22146a806dcf42b68ab12ae2d4ddee7d"
sg = SceneGraph()
parser = DeepSeekParser(API_KEY, sg)

# 📁 设置上传目录
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 🎧 加载 ASR 模型
asr_model = AutoModel(
    model="iic/SenseVoiceSmall",
    trust_remote_code=True,
    remote_code="./model.py",
    vad_model="fsmn-vad",
    vad_kwargs={"max_single_segment_time": 30000},
    device="cuda:0"
)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/voice", methods=["POST"])
def voice():
    if "audio" not in request.files:
        return jsonify({"result": "❌ 没有收到音频文件"})

    audio_file = request.files["audio"]
    webm_path = os.path.join(UPLOAD_FOLDER, "latest.webm")
    mp3_path = os.path.join(UPLOAD_FOLDER, "latest.mp3")
    audio_file.save(webm_path)

    try:
        subprocess.run(["ffmpeg", "-y", "-i", webm_path, mp3_path], check=True)

        res = asr_model.generate(
            input=mp3_path,
            cache={},
            language="auto",
            use_itn=True,
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15
        )
        text = rich_transcription_postprocess(res[0]["text"])
        parsed = parser.parse(text)

        return handle_parsed_result(parsed, text)

    except Exception as e:
        return jsonify({"result": f"❌ 音频解析失败：{e}"})

@app.route("/process", methods=["POST"])
def process():
    user_input = request.json["input"]
    try:
        parsed = parser.parse(user_input)
        return handle_parsed_result(parsed, user_input)

    except Exception as e:
        return jsonify({"result": f"❌ 解析失败：{e}"})


def handle_parsed_result(parsed, text=None):
    """根据结构化结果返回统一的响应格式"""
    if parsed.get("type") == "add" and "item" in parsed and "location_path" in parsed:
        ori_str = f"（方位：{parsed['orientation']}）" if parsed.get("orientation") else ""
        return jsonify({
            "result": f"✅ 识别并记录：{parsed['item']} -> {'/'.join(parsed['location_path'])} {ori_str}",
            "text": text or parsed['item']
        })

    elif parsed.get("type") == "merge" and "location_child" in parsed and "location_path" in parsed:
        return jsonify({
            "result": f"📍 更新结构：{'/'.join(parsed['location_path'])}/{parsed['location_child']} 已连接",
            "text": text or parsed['location_child']
        })

    elif parsed.get("type") == "query" and "item" in parsed:
        result = sg.query_item(parsed["item"])
        return jsonify({
            "result": result,
            "text": text or parsed["item"]
        })

    elif parsed.get("type") == "unknown":
        return jsonify({
            "result": f"🤖 抱歉，我没听懂你说的内容：{parsed.get('text', text)}",
            "text": parsed.get("text", text)
        })

    return jsonify({"result": f"📝 识别内容：{text}\n⚠️ 但未成功解析结构", "text": text})



@app.route("/query", methods=["POST"])
def query():
    item = request.json["item"]
    return jsonify({"result": sg.query_item(item)})

@app.route("/history", methods=["POST"])
def history():
    item = request.json["item"]
    return jsonify({"result": sg.history_of(item)})

@app.route("/structure", methods=["GET"])
def structure():
    return jsonify({"result": sg.structure()})

@app.route("/scene", methods=["GET"])
def get_scene():
    scene_info = sg.get_scene_info()  # 结构化字符串
    return jsonify({"type": "scene", "scene_info": scene_info})

if __name__ == "__main__":
    app.run(debug=True)
