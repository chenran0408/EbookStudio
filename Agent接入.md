# 把电子书任务交给 Agent

从 GitHub 发布页下载 Windows x64 ZIP 并完整解压。`EbookStudio-Agent.exe` 与图形版使用相同的书稿、网页采集、OCR 和导出功能。两个 EXE 共用同目录的 `_internal` 文件夹。 [GitHub 发布页](https://github.com/chenran0408/EbookStudio/releases)

MCP 服务器在初始化时会提供简要操作说明。需要完整示例时，可以让 Agent 调用 `ebook_guide`，传入空对象 `{}`，即可取得 Markdown 指南。项目里的 `AGENTS.md` 是可选的，可用来补充自己的要求。升级工作台后，如果旧会话里还看不到新工具，重新连接 MCP 或新建会话。

v0.8.0 提供 14 个 MCP 工具。更新后调用 `ebook_status`，确认返回的 `version` 是 `0.8.0`，并在工具列表中核对 `ebook_rule_groups`、`ebook_proofread` 和 `ebook_compare`。如果新版解压到其他文件夹，请将客户端配置中的 command 更新为新 EXE 的路径，重新连接后再查看列表。

## 在 Codex 中使用

在连接到这台 Windows 电脑的 Codex 中，把下列设置加入 `~/.codex/config.toml`。将示例路径换成 EXE 的实际绝对路径。较长的扫描任务可以提高 `tool_timeout_sec`。

~~~toml
[mcp_servers.ebook-studio]
command = "C:/Apps/EbookStudio/EbookStudio-Agent.exe"
args = ["mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 600
~~~

如果更喜欢用 CLI 注册，也可运行 `codex mcp add ebook-studio -- 'C:\Apps\EbookStudio\EbookStudio-Agent.exe' mcp`，然后在生成的配置项中补上超时。需要单独给本服务设置环境变量时，可写在 `[mcp_servers.ebook-studio.env]` 中。运行 `codex mcp list` 核对名称，再在新会话中让 Codex 调用 `ebook_status`。 [Codex MCP 配置说明](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

## 在 WorkBuddy 中使用

在支持本机 STDIO 的 WorkBuddy 桌面客户端中，打开 MCP 的自定义配置，添加本地服务器。程序填写 `EbookStudio-Agent.exe` 的绝对路径，参数填写 `mcp`。使用 JSON 配置的客户端可参考下列条目。

~~~json
{
  "mcpServers": {
    "ebook-studio": {
      "type": "stdio",
      "command": "C:/Apps/EbookStudio/EbookStudio-Agent.exe",
      "args": ["mcp"]
    }
  }
}
~~~

首次连接时，核对程序路径并在所用客户端中信任这个本地服务器。使用客户端提供的环境变量设置时，可在这个服务的 `env` 对象中填写 `EBOOK_STUDIO_OCR_MODEL`、`EBOOK_STUDIO_PADDLE_PYTHON`、`EBOOK_STUDIO_PADDLE_MODELS` 或 `EBOOK_STUDIO_EPUBCHECK` 等实际值。重新连接后，先让 WorkBuddy 调用 `ebook_status`。 [WorkBuddy MCP 指南](https://www.codebuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide) · [腾讯本机 STDIO 配置](https://cloud.tencent.com/document/product/1831/137039)

## 直接调用命令行

~~~powershell
$agent = 'C:\Apps\EbookStudio\EbookStudio-Agent.exe'
& $agent schema
& $agent run 'C:\Books\任务.json'
~~~

`schema` 显示任务字段。`run` 读取 UTF-8 JSON 任务文件，输出 JSON 结果和文件路径。输出目录应已存在。修改现有工程时，Agent 会写入指定的新工程路径，源文件仍可用原路径打开。

识别四页扫描件的任务示例。

~~~json
{
  "action": "ocr",
  "source": "C:/Books/扫描件.pdf",
  "engine": "paddle",
  "pages": "1-4",
  "output": "C:/Books/扫描稿.bookproj"
}
~~~

后续可用 `export` 把该工程导出到指定的 .epub，再用 `check` 运行 EPUBCheck。MCP 中对应的是 `ebook_ocr`、`ebook_export` 和 `ebook_check`。

可以把这样的任务交给 Agent。

> 使用电子书工作台读取这份本地扫描 PDF。用 PaddleOCR-VL-1.6 识别第 1 到 4 页，保存一份可编辑工程，再导出带目录的 EPUB。把新工程、样书路径和校验结果发给我。

OCR 模型和 EPUBCheck 需按随包使用说明安装。环境变量也可设置为 Windows 用户环境变量，保存后重启客户端。先让 Agent 用已就绪的模型试识别一页，检查文字和页序，再提交多页任务。

## 把校对或版本对比交给 Agent

> 读取我的本机规则组「杂志署名校对」，在扫描稿.bookproj 上预览。告诉我每一步实际改几处，并给出修改前后的文字。我确认后，将校订稿另存为新的工程并导出 EPUB，最后运行 EPUBCheck。

> 比较我提供的原版和修订版 EPUB，按类别列出变化，指出改过正文的章节，并给出前后文。请检查所有结果页。

规则组来自与桌面共用的本机工作区。如果客户端使用自定义目录，可在 MCP 环境变量中填写与桌面一致的 `EBOOK_STUDIO_WORKSPACE`。`ebook_guide` 中有可直接调用的参数示例。
