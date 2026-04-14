const express = require("express");
const multer = require("multer");
const axios = require("axios");
const FormData = require("form-data");
const fs = require("fs");
const path = require("path");
const { v4: uuidv4 } = require("uuid");

const app = express();
const port = 3333;

const MINERU_API_URL = process.env.MINERU_API_URL;
const PUBLIC_BASE_URL = process.env.PUBLIC_BASE_URL;
const MINERU_BACKEND = process.env.MINERU_BACKEND || "vlm-vllm-async-engine";
const BASE_IMAGE_DIR = path.join(__dirname, "temp_images");

if (!fs.existsSync(BASE_IMAGE_DIR)) {
  fs.mkdirSync(BASE_IMAGE_DIR, { recursive: true });
}

app.use("/mineru_images", express.static(BASE_IMAGE_DIR));

const upload = multer({ storage: multer.memoryStorage() });

app.post("/mineru_parse", upload.single("file"), async (req, res) => {
  try {
    if (!req.file) throw new Error("No file uploaded");

    const taskId = uuidv4();
    console.log(`[${taskId}] Start processing: ${req.file.originalname}`);

    const formData = new FormData();
    formData.append("files", req.file.buffer, {
      filename: req.file.originalname,
    });
    formData.append("backend", MINERU_BACKEND);
    formData.append("return_images", "true");

    const response = await axios.post(MINERU_API_URL, formData, {
      headers: { ...formData.getHeaders() },
      timeout: 600000,
    });

    const results = response.data.results || {};

    let finalMarkdown = "";
    let totalPages = 0;

    for (const [originalFileName, content] of Object.entries(results)) {
      let { md_content, images, pages } = content;
      totalPages += pages || 1;

      const baseName = path.parse(originalFileName).name;
      // 1. 将所有的点 . 替换为下划线 _ (解决 ... 变成 .. 的问题)
      // 2. 将所有的斜杠、反斜杠、冒号等可能引起路径问题的字符替换为下划线
      let safeFolderName = baseName.replace(/[.\/\\?%*:|"<>]/g, "_");

      // 如果文件名全是特殊字符导致脱敏后为空，则给个默认值
      if (!safeFolderName) safeFolderName = "document";

      const targetFolder = path.join(BASE_IMAGE_DIR, taskId, safeFolderName);

      if (!fs.existsSync(targetFolder)) {
        fs.mkdirSync(targetFolder, { recursive: true });
      }

      if (images && Object.keys(images).length > 0) {
        for (const [imgName, base64Data] of Object.entries(images)) {
          const imgFilePath = path.join(targetFolder, imgName);
          const base64Content = base64Data.replace(
            /^data:image\/\w+;base64,/,
            ""
          );
          fs.writeFileSync(imgFilePath, Buffer.from(base64Content, "base64"));

          const targetUrl = `${PUBLIC_BASE_URL}/${taskId}/${encodeURIComponent(
            safeFolderName
          )}/${encodeURIComponent(imgName)}`;

          // 替换 Markdown 中的图片链接
          const escapedImgName = imgName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          const regex = new RegExp(
            `!\\[.*?\\]\\(images\\/${escapedImgName}\\)`,
            "g"
          );
          md_content = md_content.replace(regex, `![image](${targetUrl})`);
        }
      }
      finalMarkdown += md_content + "\n\n";
    }

    console.log(`[${taskId}] Success. Saved to subfolder.`);

    res.json({
      success: true,
      markdown: finalMarkdown.trim(),
      pages: totalPages,
    });
  } catch (error) {
    console.error("Adapter Error:", error.message);
    res.json({
      success: false,
      markdown: "",
      pages: 0,
      error: error.message,
    });
  }
});

const server = app.listen(port, "0.0.0.0", () => {
  console.log(`MinerU Structured Adapter listening at http://0.0.0.0:${port}`);
});

server.timeout = 600000;
server.keepAliveTimeout = 650000;
server.headersTimeout = 660000;
