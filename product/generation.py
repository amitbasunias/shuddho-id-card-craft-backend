"""
ID card generation engine (ported verbatim from the FastAPI product).

These are pure, framework-agnostic helpers: SVG conversion, placeholder
extraction, photo/font embedding, cropping, card rendering, and Inkscape/cairosvg
conversions to PDF/PNG/EPS. The Django service layer (product/services.py) calls
render_card_svg + the svg_to_* converters to produce each student's outputs.

External tools/libs used (same as the original product):
  - lxml (SVG parsing)
  - cairosvg (SVG rasterization / fallback)
  - rembg (CPU background removal)
  - Pillow (image handling)
  - Inkscape CLI (preferred SVG->PDF/PNG/EPS), with cairosvg fallback
"""

import base64
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from lxml import etree

from .bijoy_converter import unicode_to_bijoy_ansi

def _fwd(path: str) -> str:
    """Return absolute path with forward slashes (required by lxml & cairosvg on Windows)."""
    return str(Path(path).resolve()).replace("\\", "/")


# ---------------------------------------------------------------------------
# SVG Engine
# ---------------------------------------------------------------------------

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"


def convert_to_svg(source_path: str, output_path: str) -> str:
    ext = Path(source_path).suffix.lower()

    if ext == ".svg":
        abs_source = str(Path(source_path).resolve())
        abs_output = str(Path(output_path).resolve())
        os.makedirs(os.path.dirname(abs_output), exist_ok=True)
        with open(abs_source, "rb") as src, open(abs_output, "wb") as dst:
            dst.write(src.read())
        if not os.path.exists(abs_output):
            raise RuntimeError(f"SVG copy failed output not found: {abs_output}")
        return abs_output

    if ext in (".eps", ".pdf"):
        inkscape = shutil.which("inkscape")
        if not inkscape:
            raise RuntimeError("Inkscape is not installed")

        abs_source = str(Path(source_path).resolve())
        abs_output = str(Path(output_path).resolve())
        os.makedirs(os.path.dirname(abs_output), exist_ok=True)

        result = subprocess.run(
            [inkscape, "--export-type=svg", f"--export-filename={abs_output}", abs_source],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Inkscape conversion failed: {result.stderr.decode()}")
        if not os.path.exists(abs_output):
            raise RuntimeError(f"Inkscape conversion failed: output not found: {abs_output}")
        return abs_output

    raise RuntimeError(f"Unsupported template extension: {ext}")


KNOWN_FIELD_SYNONYMS = {
    "student_name": "student_name",
    "name": "student_name",
    "full_name": "student_name",
    "student_id": "student_id",
    "id": "student_id",
    "id_number": "student_id",
    "father_name": "father_name",
    "father": "father_name",
    "mother_name": "mother_name",
    "mother": "mother_name",
    "class_name": "class_name",
    "class": "class_name",
    "blood_group": "blood_group",
    "blood": "blood_group",
    "address": "address",
    "date_of_birth": "date_of_birth",
    "dob": "date_of_birth",
    "birth_date": "date_of_birth",
    "mobile_number": "mobile_number",
    "mobile": "mobile_number",
    "phone": "mobile_number",
    "session": "session",
    "department": "department",
    "dept": "department",
    "designation": "designation",
    "husband_name": "husband_name",
    "husband": "husband_name",
    "section": "section",
    "roll": "roll",
    "roll_number": "roll",
    "index_no": "index_no", "index": "index_no",
    "nid_no": "nid_no", "nid": "nid_no",
    "joining_date": "joining_date", "joining": "joining_date", "join_date": "joining_date",
    "guardians_mobile": "guardians_mobile", "guardian_mobile": "guardians_mobile", "guardian": "guardians_mobile",
    "registration_no": "registration_no", "registration": "registration_no", "reg_no": "registration_no",
    "student_photo": "student_photo",
    "photo": "student_photo",
    "avatar": "student_photo",
}


def _strip_tag(tag: str) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) and "}" in tag else (tag or "")


def _extract_svg_position(el) -> Optional[Dict[str, str]]:
    position = {}
    for attr in ("x", "y", "width", "height", "transform"):
        value = el.get(attr)
        if value is not None:
            position[attr] = value
    return position or None


def extract_placeholder_ids(svg_path: str) -> Dict[str, Dict[str, Any]]:
    with open(svg_path, "rb") as f:
        data = f.read()
    root = etree.fromstring(data)
    placeholders = {}
    for el in root.iter():
        if callable(el.tag):
            continue
        el_id = (el.get("id") or "").strip()
        if not el_id:
            continue

        tag = _strip_tag(el.tag)
        if tag not in {"text", "tspan", "image", "g", "rect", "path", "ellipse", "circle", "polygon", "line", "polyline"}:
            continue

        placeholders[el_id] = {
            "element_type": tag,
            "field_name": KNOWN_FIELD_SYNONYMS.get(el_id.lower()),
            "position": _extract_svg_position(el),
            "placeholder_text": "".join(el.itertext()).strip() or None,
        }
    return placeholders


def _set_text_content(el, value: str):
    """
    Set text on a <text> or <tspan> element correctly.
    SVG text can be structured in two ways:
      1. <text id="student_name">placeholder</text>  — text directly on element
      2. <text id="student_name"><tspan>placeholder</tspan></text> — text in child tspan
    We handle both, and also clear any tail text on children to avoid duplication.
    """
    tag = _strip_tag(el.tag)
    if tag == "text":
        children = list(el)
        if children:
            children[0].text = value
            children[0].tail = None
            for child in children[1:]:
                child.text = ""
                child.tail = None
            el.text = None
        else:
            el.text = value
    elif tag == "tspan":
        el.text = value


def _embed_photo(el, photo_path: str) -> bool:
    if not photo_path or not os.path.exists(photo_path):
        return False
    ext = Path(photo_path).suffix.lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
    with open(photo_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    href = f"data:{mime};base64,{data}"
    el.set("href", href)
    el.set(f"{{{XLINK_NS}}}href", href)
    el.set("preserveAspectRatio", "xMidYMid slice")
    return True


def _apply_position_overrides(el, position: Optional[Dict[str, Any]]):
    if not isinstance(position, dict):
        return
    for attr in ("x", "y", "width", "height", "transform"):
        value = position.get(attr)
        if value is not None and value != "":
            el.set(attr, str(value))


def remove_photo_background(photo_path: str) -> str:
    """Remove background using rembg (CPU). Returns path to the processed PNG,
    or the original path on error."""
    try:
        from rembg import remove
        from PIL import Image
        import io as _io

        with open(photo_path, "rb") as f:
            img_bytes = f.read()

        result_bytes = remove(img_bytes)
        output_path = str(Path(photo_path).with_suffix("")) + "_nobg.png"
        img = Image.open(_io.BytesIO(result_bytes)).convert("RGBA")
        img.save(output_path, format="PNG")
        print(f"[BG_REMOVE] Background removed via rembg: {output_path}")
        return output_path
    except Exception as exc:
        print(f"[BG_REMOVE] rembg background removal failed ({exc}) — using original photo")
        return photo_path


# ---------------------------------------------------------------------------
# ANSI font conversion (Unicode Bengali → SutonnyMJ / Bijoy encoding)
# ---------------------------------------------------------------------------

# Fonts that use legacy ANSI Bengali encoding instead of Unicode
ANSI_BENGALI_FONTS = {"sutonnymj", "sutonny mj", "sutonny", "bijoy", "bijoy bayanno",
                      "adarsha lipi", "amar bangla", "boishakhi", "charukola", "kalpana"}


def _is_ansi_font(font_name: str) -> bool:
    return bool(font_name) and font_name.lower().strip() in ANSI_BENGALI_FONTS


# ---------------------------------------------------------------------------
# Custom font embedding helper
# ---------------------------------------------------------------------------

def _embed_custom_fonts(root, mapping_json: dict, fonts_map):
    """Embed uploaded custom fonts as base64 @font-face in SVG <defs>.

    `fonts_map` is one of:
      * a dict {font_family_name: absolute_file_path} — used by the SaaS so we
        can resolve per-user + global fonts without scanning disk; OR
      * an iterable of directory paths to scan for `<font_family>.{ttf,otf,
        woff,woff2}` (legacy single-folder behaviour, kept for compatibility).
    """
    if not mapping_json or not fonts_map:
        return
    used_fonts: set = set()
    for entry in mapping_json.values():
        if not isinstance(entry, dict):
            continue
        style = entry.get("style") or {}
        ff = style.get("font_family") or style.get("font-family", "")
        if ff:
            used_fonts.add(ff)
    if not used_fonts:
        return

    # Normalise input → resolver function `font_name -> Path | None`.
    if isinstance(fonts_map, dict):
        def _resolve(name):
            p = fonts_map.get(name)
            return Path(p) if p else None
    else:
        dirs = [Path(d) for d in ([fonts_map] if isinstance(fonts_map, (str, Path)) else fonts_map)]
        def _resolve(name):
            for d in dirs:
                for ext in (".ttf", ".otf", ".woff", ".woff2"):
                    candidate = d / f"{name}{ext}"
                    if candidate.exists():
                        return candidate
            return None

    css_parts: List[str] = []
    for font_name in used_fonts:
        font_file = _resolve(font_name)
        if not font_file or not font_file.exists():
            continue
        with open(font_file, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime_map = {".ttf": "font/truetype", ".otf": "font/otf",
                    ".woff": "font/woff", ".woff2": "font/woff2"}
        mime = mime_map.get(font_file.suffix.lower(), "font/truetype")
        css_parts.append(
            f"@font-face{{font-family:'{font_name}';src:url('data:{mime};base64,{b64}');}}"
        )
    if not css_parts:
        return
    defs = root.find(f"{{{SVG_NS}}}defs")
    if defs is None:
        defs = etree.Element(f"{{{SVG_NS}}}defs")
        root.insert(0, defs)
    style_el = defs.find(f"{{{SVG_NS}}}style")
    if style_el is None:
        style_el = etree.SubElement(defs, f"{{{SVG_NS}}}style")
        style_el.set("type", "text/css")
    existing = style_el.text or ""
    style_el.text = existing + "\n" + "\n".join(css_parts)
    print(f"[FONTS] Embedded {len(css_parts)} custom font(s) in SVG")


# ---------------------------------------------------------------------------
# Smart photo crop (top-aligned, center-horizontal)
# ---------------------------------------------------------------------------

def crop_photo_to_aspect(photo_path: str, target_w: float, target_h: float) -> str:
    """Crop photo to match placeholder aspect ratio, top-aligned and center-cropped horizontally."""
    if not photo_path or not os.path.exists(photo_path) or target_w <= 0 or target_h <= 0:
        return photo_path
    try:
        from PIL import Image
        img = Image.open(photo_path)
        img_w, img_h = img.size
        target_ratio = target_w / target_h
        img_ratio = img_w / img_h
        if abs(img_ratio - target_ratio) < 0.02:
            return photo_path  # already close enough
        if img_ratio > target_ratio:
            # Wider than target — crop left and right equally, keep top
            new_w = int(round(img_h * target_ratio))
            left = (img_w - new_w) // 2
            img = img.crop((left, 0, left + new_w, img_h))
        else:
            # Taller than target — crop bottom, keep top
            new_h = int(round(img_w / target_ratio))
            img = img.crop((0, 0, img_w, new_h))
        out_path = str(Path(photo_path).with_suffix("")) + "_cropped.png"
        img.save(out_path, format="PNG")
        print(f"[CROP] Photo cropped to {target_w}×{target_h} ratio: {out_path}")
        return out_path
    except Exception as exc:
        print(f"[CROP] Photo crop failed ({exc}), using original")
        return photo_path


def render_card_svg(
        template_svg_path: str,
        student_data: Dict[str, Any],
        photo_path: Optional[str],
        output_path: str,
        mapping_json: Optional[Dict[str, Any]] = None,
        enabled_fields: Optional[Dict[str, bool]] = None,
        fonts_config: Optional[Dict[str, Any]] = None,
        fonts_map: Optional[Any] = None,
) -> str:
    parser = etree.XMLParser(remove_blank_text=True)
    with open(template_svg_path, "rb") as f:
        tree = etree.parse(f, parser)
    root = tree.getroot()

    # Filter student_data based on enabled_fields
    enabled_fields = enabled_fields or {}
    field_map = {}

    fonts_config = fonts_config or {}

    # Embed any uploaded custom fonts as base64 @font-face in the SVG.
    # The SaaS passes a {family: path} dict computed from the Font model
    # (per-user + global). When called from outside the SaaS, callers may
    # pass a directory path / list of directories — both shapes are
    # handled by _embed_custom_fonts.
    if fonts_map:
        _embed_custom_fonts(root, mapping_json or {}, fonts_map)

    # Build field_map — include all fields from student_data, apply enabled_fields filter.
    # "name" is an alias for "student_name" for backwards-compat with SVG element IDs.
    _name_val = student_data.get("student_name") or student_data.get("name", "")
    available_fields = {
        "name":         _name_val,
        "student_name": _name_val,
        "student_id":   student_data.get("student_id", ""),
        "father_name":  student_data.get("father_name", ""),
        "mother_name":  student_data.get("mother_name", ""),
        "class_name":   student_data.get("class_name", ""),
        "blood_group":  student_data.get("blood_group", ""),
        "address":      student_data.get("address", ""),
        "date_of_birth" :student_data.get("date_of_birth", ""),
        "mobile_number" :student_data.get("mobile_number", ""),
        "session":      student_data.get("session", ""),
        "department":   student_data.get("department", ""),
        "designation":  student_data.get("designation", ""),
        "husband_name": student_data.get("husband_name", ""),
        "section":      student_data.get("section", ""),
        "roll":         student_data.get("roll", ""),
        "index_no":     student_data.get("index_no", ""),
        "nid_no":       student_data.get("nid_no", ""),
        "joining_date": student_data.get("joining_date", ""),
        "guardians_mobile": student_data.get("guardians_mobile", ""),
        "registration_no":  student_data.get("registration_no", ""),
    }
    # "name" alias should use the same enabled_fields key as "student_name"
    _enabled_alias = {"name": "student_name"}

    for field_key, field_value in available_fields.items():
        enabled_key = _enabled_alias.get(field_key, field_key)
        if not enabled_fields or enabled_fields.get(enabled_key, False) or enabled_fields.get(field_key, False):
            field_map[field_key] = field_value

    mapping_json = mapping_json or {}
    anchor_map = {"left": "start", "center": "middle", "right": "end"}
    print(f"[RENDER] Scanning SVG elements for placeholders... (enabled fields: {list(field_map.keys())})")
    for el in root.iter():
        if callable(el.tag):
            continue

        el_id = (el.get("id") or "").strip()
        if not el_id:
            continue

        tag = _strip_tag(el.tag)
        mapping_entry = mapping_json.get(el_id, {}) if isinstance(mapping_json, dict) else {}
        field_name = mapping_entry.get("field_name") if isinstance(mapping_entry, dict) else None
        if not field_name and el_id in field_map:
            field_name = el_id

        if not field_name or (field_name not in field_map and field_name != "student_photo"):
            continue

        if field_name == "student_photo":
            _apply_position_overrides(el, mapping_entry.get("position"))
            if tag == "image":
                if _embed_photo(el, photo_path):
                    print(f"[RENDER]   Embedded photo into element id={el_id!r}")
            else:
                for child in el.iter():
                    if callable(child.tag):
                        continue
                    if _strip_tag(child.tag) == "image" and _embed_photo(child, photo_path):
                        print(f"[RENDER]   Embedded photo into descendant of group id={el_id!r}")
                        break
            continue

        style_props = mapping_entry.get("style", {}) if isinstance(mapping_entry, dict) else {}
        # Add font from fonts_config if available for this field
        if field_name in fonts_config and isinstance(fonts_config[field_name], dict):
            font_name = fonts_config[field_name].get("name")
            if font_name:
                # Preserve existing style and add font
                if "font-family" not in style_props:
                    style_props["font-family"] = font_name

        position_entry = mapping_entry.get("position") if isinstance(mapping_entry, dict) else None
        _apply_position_overrides(el, position_entry)
        if tag in ("text", "tspan"):
            value = field_map.get(field_name, "")
            # Apply text-anchor as an SVG attribute (not CSS) so Inkscape PDF respects it
            text_align = (style_props or {}).pop("text_align", None) or (style_props or {}).pop("text-align", None) or "left"
            anchor_map = {"left": "start", "center": "middle", "right": "end"}
            text_anchor = anchor_map.get(text_align, "start")
            el.set("text-anchor", text_anchor)
            # Adjust x for alignment within the element's bounding box
            if position_entry:
                pos_x = float(position_entry.get("x", el.get("x") or 0))
                pos_w = float(position_entry.get("width", 0))
                if text_align == "center" and pos_w:
                    el.set("x", str(pos_x + pos_w / 2))
                elif text_align == "right" and pos_w:
                    el.set("x", str(pos_x + pos_w))
            if style_props:
                existing_style = el.get("style", "")
                style_strings = [existing_style.strip()] if existing_style else []
                for style_key, style_value in style_props.items():
                    if not style_value:
                        continue
                    css_key = style_key.replace("_", "-")
                    if css_key in ("text-align", "text_align"):
                        continue  # handled via text-anchor attribute above
                    if css_key == "font-size" and str(style_value).replace(".", "").isdigit():
                        style_value = f"{style_value}px"
                    # Split "bold italic" into two separate CSS properties
                    if css_key == "font-weight":
                        if str(style_value) == "bold italic":
                            style_strings.append("font-weight: bold")
                            style_strings.append("font-style: italic")
                            continue
                        elif str(style_value) == "italic":
                            style_strings.append("font-style: italic")
                            continue
                    style_strings.append(f"{css_key}: {style_value}")
                el.set("style", "; ".join([s for s in style_strings if s]))
            # ANSI conversion for legacy Bengali fonts
            font_family = (style_props or {}).get("font_family") or (style_props or {}).get("font-family", "")
            if _is_ansi_font(font_family):
                value = unicode_to_bijoy_ansi(value)
            print(f"[RENDER]   Found placeholder id={el_id!r} tag={tag!r} align={text_align!r} -> writing {value!r}")
            _set_text_content(el, value)
            continue

        if tag == "g":
            _apply_position_overrides(el, position_entry)
            value = field_map.get(field_name, "")
            for child in el.iter():
                if callable(child.tag):
                    continue
                child_tag = _strip_tag(child.tag)
                if child_tag in ("text", "tspan"):
                    text_align = (style_props or {}).pop("text_align", None) or (style_props or {}).pop("text-align", None) or "left"
                    child.set("text-anchor", anchor_map.get(text_align, "start"))
                    if style_props:
                        existing_style = child.get("style", "")
                        style_strings = [existing_style.strip()] if existing_style else []
                        for style_key, style_value in style_props.items():
                            if style_value and style_key not in ("text_align", "text-align"):
                                style_strings.append(f"{style_key.replace('_', '-')}: {style_value}")
                        child.set("style", "; ".join([s for s in style_strings if s]))
                    print(f"[RENDER]   Writing {field_name!r} into nested {child_tag} under group id={el_id!r}")
                    _set_text_content(child, value)
                    break
                if child_tag == "image" and field_name == "student_photo" and _embed_photo(child, photo_path):
                    print(f"[RENDER]   Embedded photo into nested image under group id={el_id!r}")
                    break

    existing_ids = set()
    for el in root.iter():
        if callable(el.tag):
            continue
        el_id = (el.get("id") or "").strip()
        if el_id:
            existing_ids.add(el_id)

    for el_id, mapping_entry in (mapping_json or {}).items():
        if el_id in existing_ids:
            continue
        if not isinstance(mapping_entry, dict):
            continue
        field_name = mapping_entry.get("field_name")
        if not field_name or (field_name not in field_map and field_name != "student_photo"):
            continue

        position = mapping_entry.get("position", {})
        style_props = mapping_entry.get("style", {})
        element_type = mapping_entry.get("element_type", "text")

        # Add font from fonts_config if available for this field
        if field_name in fonts_config and isinstance(fonts_config[field_name], dict):
            font_name = fonts_config[field_name].get("name")
            if font_name and "font-family" not in style_props:
                style_props["font-family"] = font_name

        shape = mapping_entry.get("shape", "rect")

        if field_name == "student_photo" or element_type == "image":
            new_el = etree.Element(f"{{{SVG_NS}}}image")
            new_el.set("id", el_id)
            _apply_position_overrides(new_el, position)

            # Circle crop via SVG clipPath
            if shape == "circle" and position:
                px = float(position.get("x", 0))
                py = float(position.get("y", 0))
                pw = float(position.get("width", 60))
                ph = float(position.get("height", 60))
                r = min(pw, ph) / 2
                clip_id = f"clip_{el_id}"
                defs = root.find(f"{{{SVG_NS}}}defs")
                if defs is None:
                    defs = etree.Element(f"{{{SVG_NS}}}defs")
                    root.insert(0, defs)
                clip_el = etree.SubElement(defs, f"{{{SVG_NS}}}clipPath")
                clip_el.set("id", clip_id)
                circle_el = etree.SubElement(clip_el, f"{{{SVG_NS}}}circle")
                circle_el.set("cx", str(px + pw / 2))
                circle_el.set("cy", str(py + ph / 2))
                circle_el.set("r", str(r))
                new_el.set("clip-path", f"url(#{clip_id})")

            if field_name == "student_photo":
                if _embed_photo(new_el, photo_path):
                    root.append(new_el)
                    print(f"[RENDER]   Created photo placeholder id={el_id!r} shape={shape!r}")
            else:
                root.append(new_el)
                print(f"[RENDER]   Created image placeholder id={el_id!r}")
            continue

        # Text element — dominant-baseline=hanging makes y refer to the TOP of the text,
        # matching Konva's coordinate convention where y is the top-left corner.
        text_align = style_props.pop("text_align", None) or style_props.pop("text-align", None) or "left"
        text_anchor = anchor_map.get(text_align, "start")

        pos_x = float(position.get("x", 0)) if position else 0
        pos_w = float(position.get("width", 60)) if position else 60
        if text_align == "center":
            text_x = pos_x + pos_w / 2
        elif text_align == "right":
            text_x = pos_x + pos_w
        else:
            text_x = pos_x

        new_el = etree.Element(f"{{{SVG_NS}}}text")
        new_el.set("id", el_id)
        new_el.set("dominant-baseline", "hanging")
        new_el.set("text-anchor", text_anchor)
        adj_position = dict(position) if position else {}
        adj_position["x"] = str(text_x)
        _apply_position_overrides(new_el, adj_position)
        if style_props:
            style_strings = []
            for style_key, style_value in style_props.items():
                if not style_value:
                    continue
                css_key = style_key.replace("_", "-")
                if css_key == "font-size" and str(style_value).replace(".", "").isdigit():
                    style_value = f"{style_value}px"
                # Split "bold italic" into two separate CSS properties
                if css_key == "font-weight":
                    if str(style_value) == "bold italic":
                        style_strings.append("font-weight: bold")
                        style_strings.append("font-style: italic")
                        continue
                    elif str(style_value) == "italic":
                        style_strings.append("font-style: italic")
                        continue
                style_strings.append(f"{css_key}: {style_value}")
            if style_strings:
                new_el.set("style", "; ".join(style_strings))
        # ANSI conversion for legacy Bengali fonts
        text_value = field_map.get(field_name, "")
        font_family = style_props.get("font_family") or style_props.get("font-family", "")
        if _is_ansi_font(font_family):
            text_value = unicode_to_bijoy_ansi(text_value)
        _set_text_content(new_el, text_value)
        root.append(new_el)
        print(f"[RENDER]   Created text placeholder id={el_id!r} field={field_name!r} align={text_align!r}")

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tree.write(output_path, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    print(f"[RENDER] SVG written to {output_path}")
    return output_path


def _inkscape_bin() -> str:
    """Find Inkscape executable on Windows or PATH."""
    candidates = [
        r"C:\Program Files\Inkscape\bin\inkscape.exe",
        r"C:\Program Files (x86)\Inkscape\bin\inkscape.exe",
        "inkscape",
    ]
    return next((c for c in candidates if os.path.exists(c)), "inkscape")


def _svg_fix_for_pdf(svg_path: str) -> str:
    """
    Produce a patched copy of the SVG for Inkscape PDF export:

    1. Width/height units: Inkscape treats unitless SVG dimensions as mm, so a
       card designed at 191px Ã— 257px gets exported as a ~A4 page.  Adding the
       'px' suffix forces Inkscape to use CSS pixel units (96 dpi).

    2. dominant-baseline="hanging": Inkscape's PDF backend ignores this, shifting
       text up.  We replace it with an explicit dy shift equivalent to the font
       ascent so the baseline lands in the right place in the PDF.
    """
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(svg_path, parser)
    root = tree.getroot()

    # --- Fix 1: add 'px' to unitless width / height on the root element ---
    _known_units = ("px", "mm", "cm", "in", "pt", "em", "ex", "%")
    for attr in ("width", "height"):
        val = root.get(attr, "")
        if val and not any(val.endswith(u) for u in _known_units):
            root.set(attr, f"{val}px")

    # --- Fix 2: replace dominant-baseline="hanging" with dy offset ---
    for el in root.iter():
        if callable(el.tag):
            continue
        if _strip_tag(el.tag) != "text":
            continue
        if el.get("dominant-baseline") != "hanging":
            continue

        fs_val = 14.0
        style_str = el.get("style", "")
        for part in style_str.split(";"):
            k, _, v = part.partition(":")
            if k.strip() == "font-size":
                try:
                    fs_val = float(v.strip().replace("px", "").replace("pt", "").strip())
                except ValueError:
                    pass
                break
        fs_attr = el.get("font-size")
        if fs_attr:
            try:
                fs_val = float(str(fs_attr).replace("px", "").replace("pt", "").strip())
            except ValueError:
                pass

        el.set("dy", f"{fs_val * 0.75}")
        el.attrib.pop("dominant-baseline", None)

    patched_path = svg_path.replace(".svg", "_pdf_patch.svg")
    tree.write(patched_path, xml_declaration=True, encoding="UTF-8")
    return patched_path


def svg_to_pdf(svg_path: str, pdf_path: str) -> str:
    patched_svg = _svg_fix_for_pdf(svg_path)
    abs_svg = str(Path(patched_svg).resolve())
    abs_pdf = str(Path(pdf_path).resolve())
    os.makedirs(os.path.dirname(abs_pdf), exist_ok=True)
    print(f"[GEN]   inkscape svg->pdf: {abs_svg} -> {abs_pdf}")
    result = subprocess.run(
        [_inkscape_bin(), "--export-type=pdf", "--export-area-page",
         f"--export-filename={abs_pdf}", abs_svg],
        capture_output=True, timeout=60,
    )
    try:
        os.remove(patched_svg)
    except OSError:
        pass
    if result.returncode != 0:
        raise RuntimeError(f"Inkscape SVG->PDF failed: {result.stderr.decode()}")
    return pdf_path


def svg_to_png(svg_path: str, png_path: str, width: int = 1200) -> str:
    abs_svg = str(Path(svg_path).resolve())
    abs_png = str(Path(png_path).resolve())
    os.makedirs(os.path.dirname(abs_png), exist_ok=True)
    print(f"[GEN]   inkscape svg->png: {abs_svg} -> {abs_png}")
    result = subprocess.run(
        [_inkscape_bin(), f"--export-width={width}", "--export-type=png",
         f"--export-filename={abs_png}", abs_svg],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Inkscape SVG->PNG failed: {result.stderr.decode()}")
    return png_path


def svg_to_eps(svg_path: str, eps_path: str) -> str:
    abs_svg = str(Path(svg_path).resolve())
    abs_eps = str(Path(eps_path).resolve())
    os.makedirs(os.path.dirname(abs_eps), exist_ok=True)
    print(f"[GEN]   inkscape svg->eps: {abs_svg} -> {abs_eps}")
    result = subprocess.run(
        [_inkscape_bin(), "--export-type=eps", f"--export-filename={abs_eps}", abs_svg],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Inkscape SVG->EPS failed: {result.stderr.decode()}")
    return eps_path
