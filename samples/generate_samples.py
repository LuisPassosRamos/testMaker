"""Generate synthetic exams used for manual testing and automated tests.

All content is invented. The set covers objective and open questions, a support text
shared by two questions, an image, a table, symbols and an equation, Word automatic
numbering, a two-column PDF, a scanned PDF, phone photos and a low-quality photo.

Run from the repository root (needs LibreOffice or Word for the PDF samples):

    backend\\.venv\\Scripts\\python samples\\generate_samples.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from app.config import Settings  # noqa: E402
from app.rendering.converter import converter_status, docx_to_pdf  # noqa: E402

FONT = "Arial"


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def base_document() -> Document:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(2))
    style = doc.styles["Normal"]
    style.font.name = FONT
    style.font.size = Pt(11)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    return doc


def add_numbering(doc: Document) -> tuple[str, str]:
    """Create a Word list with "1." questions and "a)" alternatives; returns (question numId, alt numId)."""
    numbering = doc.part.numbering_part.element
    abstract = parse_xml(
        f'<w:abstractNum xmlns:w="{qn("w:p").split("}")[0][1:]}" w:abstractNumId="90">'
        '<w:multiLevelType w:val="hybridMultilevel"/>'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
        '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr></w:lvl>'
        '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%2)"/>'
        '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>'
        "</w:abstractNum>"
    )
    first_num = numbering.find(qn("w:num"))
    if first_num is not None:
        first_num.addprevious(abstract)
    else:
        numbering.append(abstract)
    num = parse_xml(
        f'<w:num xmlns:w="{qn("w:p").split("}")[0][1:]}" w:numId="90"><w:abstractNumId w:val="90"/></w:num>'
    )
    numbering.append(num)
    return "90", "90"


def numbered(doc: Document, text_runs: list[tuple[str, dict]], num_id: str, level: int):
    p = doc.add_paragraph()
    ppr = p._p.get_or_add_pPr()
    numpr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), str(level))
    nid = OxmlElement("w:numId")
    nid.set(qn("w:val"), num_id)
    numpr.append(ilvl)
    numpr.append(nid)
    ppr.append(numpr)
    for text, fmt in text_runs:
        run = p.add_run(text)
        run.bold = fmt.get("bold")
        run.italic = fmt.get("italic")
        run.underline = fmt.get("underline")
        run.font.superscript = fmt.get("sup")
        run.font.subscript = fmt.get("sub")
    return p


def chart_image() -> bytes:
    img = Image.new("RGB", (900, 520), "white")
    draw = ImageDraw.Draw(img)
    draw.line((80, 450, 860, 450), fill="black", width=3)
    draw.line((80, 450, 80, 40), fill="black", width=3)
    values = [("Seg", 12), ("Ter", 20), ("Qua", 8), ("Qui", 16), ("Sex", 24)]
    for i, (label, value) in enumerate(values):
        x0 = 120 + i * 145
        draw.rectangle((x0, 450 - value * 16, x0 + 90, 450), fill=(70, 110, 200), outline="black")
        draw.text((x0 + 25, 465), label, fill="black", font=_font(28))
        draw.text((x0 + 30, 450 - value * 16 - 38), str(value), fill="black", font=_font(26))
    draw.text((300, 0), "Livros lidos na semana", fill="black", font=_font(30))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def header_table(doc: Document, title: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("ESCOLA MUNICIPAL EXEMPLO DAS FLORES")
    r.bold = True
    t = doc.add_table(rows=2, cols=2)
    t.style = "Table Grid"
    t.cell(0, 0).text = "Aluno(a): ______________________________"
    t.cell(0, 1).text = "Data: ___/___/______"
    t.cell(1, 0).text = "Professor(a): Ana Souza"
    t.cell(1, 1).text = "Turma: 5º ano A"
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(title)
    r.bold = True


def make_math_docx(path: Path) -> dict:
    doc = base_document()
    q_num, _ = add_numbering(doc)
    header_table(doc, "AVALIAÇÃO DE MATEMÁTICA – 2ª UNIDADE")
    doc.add_paragraph("INSTRUÇÕES:").runs[0].bold = True
    doc.add_paragraph("Leia com atenção cada questão antes de responder.")
    doc.add_paragraph("Use caneta azul ou preta e não use corretivo.")

    numbered(doc, [("Quanto é ", {}), ("2", {}), ("³", {}), (" + 4 × 5?", {})], q_num, 0)
    for alt in ("28", "32", "48", "100"):
        numbered(doc, [(alt, {})], q_num, 1)

    numbered(doc, [("Assinale a alternativa ", {}), ("INCORRETA", {"bold": True, "underline": True}),
                   (" sobre frações:", {})], q_num, 0)
    for alt in ("½ é maior que ¼", "⅓ + ⅓ = ⅔", "0,5 é igual a ½", "¾ é menor que ½"):
        numbered(doc, [(alt, {})], q_num, 1)

    doc.add_paragraph("Observe o gráfico para responder às questões 3 e 4.")
    doc.add_picture(io.BytesIO(chart_image()), width=Cm(12))
    numbered(doc, [("Em qual dia foram lidos mais livros?", {})], q_num, 0)
    for alt in ("Segunda-feira", "Terça-feira", "Quinta-feira", "Sexta-feira"):
        numbered(doc, [(alt, {})], q_num, 1)
    numbered(doc, [("Quantos livros foram lidos ao todo na semana? Mostre o cálculo.", {})], q_num, 0)
    for _ in range(3):
        doc.add_paragraph("_" * 70)

    numbered(doc, [("Resolva a expressão: ", {})], q_num, 0)
    omml = parse_xml(
        '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        "<m:f><m:num><m:r><m:t>3</m:t></m:r></m:num><m:den><m:r><m:t>4</m:t></m:r></m:den></m:f>"
        "<m:r><m:t>+</m:t></m:r>"
        "<m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>"
        "</m:oMath>"
    )
    doc.paragraphs[-1]._p.append(omml)
    doc.add_paragraph("Considere x = 2. Use π ≈ 3,14 se necessário; lembre que √16 = 4 e que 5 ≥ 3 e 2 ≤ 7.")
    for alt in ("4¾", "5", "4,75", "7"):
        numbered(doc, [(alt, {})], q_num, 1)

    numbered(doc, [("A tabela mostra o preço de alguns produtos. Quanto custam 2 cadernos e 1 lápis?", {})], q_num, 0)
    table = doc.add_table(rows=4, cols=2)
    table.style = "Table Grid"
    for i, (a, b) in enumerate([("Produto", "Preço"), ("Caderno", "R$ 12,50"), ("Lápis", "R$ 1,75"),
                                ("Borracha", "R$ 2,00")]):
        table.cell(i, 0).text = a
        table.cell(i, 1).text = b
    for alt in ("R$ 14,25", "R$ 26,75", "R$ 25,00", "R$ 16,25"):
        numbered(doc, [(alt, {})], q_num, 1)
    doc.save(path)
    return {"questions": ["1.", "2.", "3.", "4.", "5.", "6."], "alternatives": {"1.": 4, "2.": 4, "3.": 4,
            "4.": 0, "5.": 4, "6.": 4}, "has_image": True, "has_table": True,
            "must_contain": ["INCORRETA", "π ≈ 3,14", "√16 = 4", "5 ≥ 3", "2 ≤ 7", "½ é maior que ¼"]}


def make_portuguese_docx(path: Path) -> dict:
    doc = base_document()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("ATIVIDADE DE LÍNGUA PORTUGUESA").bold = True
    doc.add_paragraph("Nome: __________________________________  Turma: ______")
    doc.add_paragraph("Leia o texto para responder às questões 01 e 02.")
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t.add_run("A cigarra e a formiga").italic = True
    doc.add_paragraph(
        "Durante o verão, a cigarra cantava sem parar, enquanto a formiga trabalhava guardando comida "
        "para o inverno. Quando o frio chegou, a cigarra não tinha o que comer e foi pedir ajuda à vizinha."
    )
    doc.add_paragraph("01 – Quem são as personagens da fábula?")
    for _ in range(3):
        doc.add_paragraph("_" * 60)
    p = doc.add_paragraph("02 – Por que a cigarra ")
    p.add_run("NÃO").bold = True
    p.add_run(" tinha comida no inverno?")
    for alt in ("a) Porque a formiga roubou.", "b) Porque passou o verão cantando.", "c) Porque estava doente.",
                "d) Porque o inverno chegou cedo."):
        doc.add_paragraph(alt)
    doc.add_paragraph("03 – Marque V para verdadeiro e F para falso:")
    for item in ("( ) A formiga trabalhou no verão.", "( ) A cigarra guardou comida.",
                 "( ) A história acontece no inverno e no verão."):
        doc.add_paragraph(item)
    p = doc.add_paragraph("04 – Complete com ")
    p.add_run("ç").underline = True
    p.add_run(" ou ")
    p.add_run("ss").underline = True
    p.add_run(": pa__aro, a__úcar, pá__aro, ca__a.")
    doc.add_paragraph("05 – Quantas sílabas tem a palavra “formiga”? a) 2   b) 3   c) 4   d) 5")
    doc.add_paragraph("06 – Escreva uma frase usando o 1º e o 2º verbo do texto. (H₂O é a fórmula da água.)")
    for _ in range(4):
        doc.add_paragraph("_" * 60)
    doc.save(path)
    return {"questions": ["01 –", "02 –", "03 –", "04 –", "05 –", "06 –"],
            "alternatives": {"01 –": 0, "02 –": 4, "03 –": 3, "04 –": 0, "05 –": 4, "06 –": 0},
            "must_contain": ["NÃO", "cigarra", "1º", "H₂O", "pa__aro"]}


def make_science_two_columns_docx(path: Path) -> dict:
    doc = base_document()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("REVISÃO DE CIÊNCIAS – 3ª UNIDADE").bold = True
    doc.add_paragraph("Nome: ________________________________________  Data: ___/___/____")
    section = doc.add_section(WD_SECTION.CONTINUOUS)
    cols = section._sectPr.xpath("./w:cols")
    col = cols[0] if cols else OxmlElement("w:cols")
    col.set(qn("w:num"), "2")
    col.set(qn("w:space"), "567")
    if not cols:
        section._sectPr.append(col)
    questions = [
        ("O que as plantas produzem na fotossíntese?", ["Gás carbônico", "Oxigênio e glicose", "Apenas água",
                                                       "Sal mineral"]),
        ("Qual é o estado físico da água no gelo?", ["Sólido", "Líquido", "Gasoso", "Plasma"]),
        ("Qual órgão bombeia o sangue pelo corpo?", ["Pulmão", "Estômago", "Coração", "Fígado"]),
        ("A água ferve, ao nível do mar, a aproximadamente:", ["50 °C", "75 °C", "100 °C", "150 °C"]),
        ("Qual destes animais é um mamífero?", ["Tubarão", "Baleia", "Pinguim", "Jacaré"]),
        ("O Sol é classificado como:", ["Planeta", "Satélite", "Estrela", "Cometa"]),
        ("Os seres que produzem o próprio alimento são chamados de:", ["Consumidores", "Decompositores",
                                                                       "Produtores", "Parasitas"]),
        ("Qual gás é essencial para a nossa respiração?", ["Nitrogênio", "Oxigênio", "Hélio", "Neônio"]),
    ]
    for i, (statement, alternatives) in enumerate(questions, 1):
        p = doc.add_paragraph()
        p.add_run(f"{i}) ").bold = True
        p.add_run(statement)
        for letter, alt in zip("abcd", alternatives, strict=True):
            doc.add_paragraph(f"{letter}) {alt}")
    doc.add_paragraph("9) Explique, com suas palavras, o que é a cadeia alimentar.")
    for _ in range(4):
        doc.add_paragraph("_" * 38)
    doc.save(path)
    labels = [f"{i})" for i in range(1, 10)]
    return {"questions": labels, "alternatives": {**{f"{i})": 4 for i in range(1, 9)}, "9)": 0},
            "two_columns": True, "must_contain": ["fotossíntese", "100 °C", "Oxigênio e glicose"]}


def to_pdf(docx: Path, out: Path, settings: Settings) -> Path | None:
    if not converter_status(settings).available:
        print("  ! no converter available, skipping", out.name)
        return None
    pdf = docx_to_pdf(docx, out.parent / "_tmp", settings)
    pdf.replace(out)
    (out.parent / "_tmp").rmdir()
    return out


def pdf_pages(pdf: Path, dpi: int) -> list[Image.Image]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        return [doc[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(doc))]
    finally:
        doc.close()


def degrade(img: Image.Image, *, angle: float, noise: float, blur: float, light: bool, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    out = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor="white")
    if blur:
        out = out.filter(ImageFilter.GaussianBlur(blur))
    arr = np.asarray(out).astype(np.float32)
    if light:
        h, w = arr.shape[:2]
        gradient = np.linspace(0.75, 1.0, w)[None, :, None] * np.linspace(0.85, 1.0, h)[:, None, None]
        arr = arr * gradient + 20 * (1 - gradient)
    if noise:
        arr = arr + rng.normal(0, noise, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def main() -> None:
    settings = Settings()
    out = HERE
    manifest: dict[str, dict] = {}
    print("Generating synthetic samples in", out)

    manifest["prova-matematica.docx"] = make_math_docx(out / "prova-matematica.docx")
    manifest["atividade-portugues.docx"] = make_portuguese_docx(out / "atividade-portugues.docx")

    math_pdf = to_pdf(out / "prova-matematica.docx", out / "prova-matematica.pdf", settings)
    if math_pdf:
        manifest["prova-matematica.pdf"] = manifest["prova-matematica.docx"] | {"has_table": True}
        pages = pdf_pages(math_pdf, 200)
        scanned = [degrade(p, angle=1.2, noise=6, blur=0.6, light=False, seed=i) for i, p in enumerate(pages)]
        scanned[0].save(out / "prova-digitalizada.pdf", save_all=True, append_images=scanned[1:], resolution=200)
        manifest["prova-digitalizada.pdf"] = {"questions": manifest["prova-matematica.docx"]["questions"],
                                              "ocr": True}

    tmp_docx = out / "_duas-colunas.docx"
    manifest_cols = make_science_two_columns_docx(tmp_docx)
    cols_pdf = to_pdf(tmp_docx, out / "revisao-ciencias-duas-colunas.pdf", settings)
    tmp_docx.unlink(missing_ok=True)
    if cols_pdf:
        manifest["revisao-ciencias-duas-colunas.pdf"] = manifest_cols
        port_pdf = to_pdf(out / "atividade-portugues.docx", out / "_port.pdf", settings)
        if port_pdf:
            photos = pdf_pages(port_pdf, 160)
            for i, page in enumerate(photos[:2]):
                photo = degrade(page, angle=-2.0 + i, noise=5, blur=0.8, light=True, seed=10 + i)
                photo.save(out / f"foto-pagina-{i + 1}.jpg", quality=72)
            low = degrade(photos[0].resize((photos[0].width // 2, photos[0].height // 2)), angle=3, noise=18,
                          blur=1.6, light=True, seed=99)
            low.save(out / "foto-baixa-qualidade.jpg", quality=35)
            manifest["foto-pagina-1.jpg"] = {"ocr": True, "questions": ["01 –", "02 –"]}
            manifest["foto-baixa-qualidade.jpg"] = {"ocr": True, "low_quality": True}
            port_pdf.unlink(missing_ok=True)

    (out / "expected.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Done:", ", ".join(sorted(manifest)))


if __name__ == "__main__":
    main()
