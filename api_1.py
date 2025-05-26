from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import os
import time
from typing import Tuple
from PIL import Image
import pdfplumber
from docx import Document
import easyocr
from typing import List

import pdfplumber
import requests
import json
import time
import os
from typing import List, Dict, Any, Tuple
from docx import Document
import easyocr
from PIL import Image
import numpy as np

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="医疗报告摘要生成服务", version="1.0.0")

# 允许跨域请求的域名列表
origins = [
    "http://localhost:3100",  # 前端开发服务器
    "http://localhost:8080",  # 其他可能的前端地址
    # 添加生产环境域名
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有 HTTP 方法（包括 OPTIONS）
    allow_headers=["*"],  # 允许所有请求头
)

# 常量定义
SUPPORTED_EXTENSIONS = {
    'pdf': ['.pdf'],
    'word': ['.docx', '.doc'],
    'image': ['.png', '.jpg', '.jpeg', '.bmp']
}
TEMP_DIR = 'temp_uploads'

class MedicalReportSummarizer:
    def __init__(self):
        self.api_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
        self.api_key = "sk-f6c68712c4f44b01a57c758e7fa18be5"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        self.messages = [{"role": "user", "content": []}]

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """从PDF提取文本"""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                text = "\n\n".join([page.extract_text() for page in pdf.pages if page.extract_text()])
            self.messages[0]["content"].append({"type": "text", "text": text})
            return text
        except Exception as e:
            print(f"PDF提取错误: {e}")
            raise HTTPException(status_code=500, detail="PDF文本提取失败")

    def extract_text_from_docx(self, docx_path: str) -> str:
        """从Word提取文本"""
        try:
            doc = Document(docx_path)
            text = "\n".join([para.text for para in doc.paragraphs])
            self.messages[0]["content"].append({"type": "text", "text": text})
            return text
        except Exception as e:
            print(f"Word提取错误: {e}")
            raise HTTPException(status_code=500, detail="Word文本提取失败")

    def extract_text_from_image(self, image_path: str) -> str:
        """从图片提取文本（使用EasyOCR）"""
        try:
            reader = easyocr.Reader(["en", "ch_sim"])  # 支持中英文
            img = Image.open(image_path)
            results = reader.readtext(np.array(img))
            text = "\n".join([result[1] for result in results])
            self.messages[0]["content"].append({"type": "image", "image": img})  # 传递图像对象
            return text
        except Exception as e:
            print(f"图片提取错误: {e}")
            raise HTTPException(status_code=500, detail="图片文本提取失败")

    def generate_summary(self, prompt: str) -> str:
        """生成摘要"""
        data = {
            "model": "qwen-max",
            "input": {
                "prompt": prompt
            },
            "parameters": {
                "temperature": 0.1,
                "top_p": 0.8,
                "max_length": 1000
            }
        }
        response = requests.post(self.api_url, headers=self.headers, data=json.dumps(data))
        response.raise_for_status()
        result = response.json()

        # print(result)
        
        if "output" in result and "text" in result["output"]:
            return result["output"]["text"].strip()

    def process_file(self, file_path: str, file_type: str) -> Tuple[str, str]:
        """处理文件主流程"""
        if file_type == 'pdf':
            full_text = self.extract_text_from_pdf(file_path)
        elif file_type == 'word':
            full_text = self.extract_text_from_docx(file_path)
        elif file_type == 'image':
            full_text = self.extract_text_from_image(file_path)
        else:
            raise HTTPException(
                status_code=400, 
                detail="支持的文件类型：PDF (.pdf)、Word (.docx/.doc)、图片 (.png/.jpg/.jpeg/.bmp)"
            )
        
        if not full_text:
            raise HTTPException(status_code=400, detail="无法从文件中提取有效内容")
        
        prompt = """你是一名专业的医疗助手。请阅读报告内容，并提取关键信息摘要：
        请提供一个简洁的摘要，包括就诊时间time、病人姓名name sex age、体检机构institution、体检结论conclusion、体检建议suggest。
        要求：1、体检建议20字以内，并通过摘要生成病人的标签
            2、输出Json格式，字段包括time name sex age institution conclusion suggestion"""
        
        summary = self.generate_summary(prompt)
        return self._format_output(file_path, summary), summary

    def _format_output(self, file_path: str, summary: str) -> str:
        """格式化输出结果"""
        return f"""
        体检报告摘要
        ================
        报告来源: {os.path.basename(file_path)}
        处理时间: {time.strftime("%Y-%m-%d %H:%M:%S")}
        ### 最终摘要
        {summary}
        """

summarizer = MedicalReportSummarizer()

@app.post("/generate-summary")
async def generate_summary(files: List[UploadFile] = File(...)):
    """合并多个文件内容生成单个摘要"""
    try:
        all_text = ""
        temp_files = []

        if not os.path.exists(TEMP_DIR):
            os.makedirs(TEMP_DIR)

        # 1. 提取所有文件的文本内容
        for file in files:
            # 保存临时文件
            file_path = os.path.join(TEMP_DIR, file.filename)
            with open(file_path, "wb") as f:
                f.write(await file.read())
            temp_files.append(file_path)

            # 识别文件类型并提取文本
            file_type = get_file_type(file.filename)
            if file_type == "pdf":
                all_text += summarizer.extract_text_from_pdf(file_path) + "\n\n"
            elif file_type == "word":
                all_text += summarizer.extract_text_from_docx(file_path) + "\n\n"
            elif file_type == "image":
                all_text += summarizer.extract_text_from_image(file_path) + "\n\n"

        # 2. 清理临时文件（提取文本后立即删除，节省内存）
        for path in temp_files:
            os.remove(path)

        #3. 生成合并后的摘要
        # prompt = f"""你是一名专业的医疗助手。请综合以下报告内容，生成统一摘要：
        # 要求包括病人信息、体检机构、总体体检结论、综合建议（20字以内），并生成病人标签。
        # 报告内容：
        # {all_text}"""
        prompt = f"""你是一名专业的医疗助手。请阅读报告内容，并提取关键信息摘要：
        请提供一个简洁的摘要，包括就诊时间time、病人姓名name sex age、体检机构institution、体检结论conclusion、体检建议suggest。
        要求：1、体检建议20字以内，并通过摘要生成病人的标签tag
            2、输出Json格式，字段包括time name sex age institution conclusion suggestion tag
        报告内容：
        {all_text}"""

        combined_summary = summarizer.generate_summary(prompt)
        # 移除 Markdown 标记，提取真正的 JSON 内容
        cleaned_json = combined_summary.replace("```json", "").replace("```", "").strip()


        try:
            # 验证 JSON 格式（可选）
            data_dict = json.loads(cleaned_json)
            print(data_dict)
            summary_data = data_dict
        except json.JSONDecodeError as e:
            # 处理解析错误（实际项目中建议记录日志）
            summary_data = {"error": "生成的摘要格式不正确"}

        return JSONResponse({
            "status": "success",
            "summary": summary_data
        })

    except Exception as e:
        # 清理所有临时文件（即使中途出错）
        for path in temp_files:
            if os.path.exists(path):
                os.remove(path)
        return JSONResponse(
            status_code=500,
            content={
                "error": f"合并处理失败: {str(e)}",
                "file_count": len(files)
            }
        )

def get_file_type(filename: str) -> str:
    """统一文件类型判断"""
    ext = os.path.splitext(filename)[1].lower()
    for file_type, exts in SUPPORTED_EXTENSIONS.items():
        if ext in exts:
            return file_type
    return 'unknown'

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")