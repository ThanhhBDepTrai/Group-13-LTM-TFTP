import zipfile
from xml.sax.saxutils import escape

docx_path = r"E:\VSCode\TFTP-Pro\docs\phan_chia_cong_viec.docx"

rows = [
    ("Thành viên", "Vai trò", "File sở hữu", "Trách nhiệm chính", "Giờ"),
    ("TV1", "Protocol & QA Lead", "tftp_packet.py, tests/test_packet.py, docs/protocol.md", "Gói tin, opcode, mã lỗi, option/OACK; test gói tin; kiểm tra RFC", "~40"),
    ("TV2 (Nhóm trưởng)", "Server & Release Engineer", "tftp_server.py, scripts/build_exe.ps1, .github/workflows/ci.yml, pyproject.toml", "Server UDP, thread pool, TID, an toàn đường dẫn; CI, đóng gói, release; điều phối merge", "~45"),
    ("TV3", "Client/GUI & Documentation", "tftp_client.py, tftp_gui.py, README.md, docs/images/", "Client core upload/download, retry/TID, progress/cancel; GUI Tkinter; README, ảnh, demo", "~45"),
]

# Build simple document.xml
def cell(text, bold=False):
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:tc><w:p><w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p></w:tc>'

table_rows = []
for i, row in enumerate(rows):
    bold = (i == 0)
    cells = "".join(cell(c, bold=bold) for c in row)
    table_rows.append(f"<w:tr>{cells}</w:tr>")

body = f"""
<w:body>
  <w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>PHÂN CÔNG CÔNG VIỆC - TFTP PRO (NHÓM 3)</w:t></w:r></w:p>
  <w:p><w:r><w:t>Mục tiêu: xây dựng ứng dụng TFTP hoàn chỉnh theo RFC 1350, có test, CI, release và tài liệu.</w:t></w:r></w:p>
  <w:tbl>
    <w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:color="000000"/><w:left w:val="single" w:sz="4" w:color="000000"/><w:bottom w:val="single" w:sz="4" w:color="000000"/><w:right w:val="single" w:sz="4" w:color="000000"/><w:insideH w:val="single" w:sz="4" w:color="000000"/><w:insideV w:val="single" w:sz="4" w:color="000000"/></w:tblBorders></w:tblPr>
    <w:tblGrid><w:gridCol w:w="1200"/><w:gridCol w:w="1800"/><w:gridCol w:w="2600"/><w:gridCol w:w="3600"/><w:gridCol w:w="800"/></w:tblGrid>
    {''.join(table_rows)}
  </w:tbl>
  <w:p><w:r><w:t></w:t></w:r></w:p>
  <w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Nguyên tắc:</w:t></w:r></w:p>
  <w:p><w:r><w:t>- Mỗi người làm trên nhánh riêng: feat/tv1-packet, feat/tv2-server, feat/tv3-gui.</w:t></w:r></w:p>
  <w:p><w:r><w:t>- PR vào main chỉ khi CI xanh và có 1 approve.</w:t></w:r></w:p>
  <w:p><w:r><w:t>- Nhóm trưởng (TV2) điều phối merge, release và xử lý sự cố.</w:t></w:r></w:p>
  <w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>
</w:body>
"""

content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
doc_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
{body}
</w:document>
"""

with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", content_types)
    z.writestr("_rels/.rels", rels)
    z.writestr("word/document.xml", doc_xml)

print("created", docx_path)
