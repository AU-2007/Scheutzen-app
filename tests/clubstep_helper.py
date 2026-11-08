import re, html as _h
def confirm_clubs(post_fn, form):
    """Vereins-Zuordnungsschritt des Imports mit den Vorschlägen der App bestätigen."""
    step = post_fn(form).get_data(as_text=True)
    rows = re.findall(r'name="cn_(\d+)" value="([^"]*)"', step)
    f = dict(form, clubs_chosen="1", club_count=str(len(rows)))
    for i, v in rows:
        f[f"cn_{i}"] = _h.unescape(v)
        sel = re.search(rf'name="cc_{i}".*?<option value="(\w+)" selected', step, re.S)
        f[f"cc_{i}"] = sel.group(1) if sel else "skip"
        seg = step.split(f'name="cv_{i}"')[1].split("</select>")[0] if f'name="cv_{i}"' in step else ""
        m = re.search(r'<option value="(\w+)" selected', seg)
        f[f"cv_{i}"] = m.group(1) if m else "new"
        n = re.search(rf'name="cnew_{i}" value="([^"]*)"', step)
        f[f"cnew_{i}"] = _h.unescape(n.group(1)) if n else ""
    return f
