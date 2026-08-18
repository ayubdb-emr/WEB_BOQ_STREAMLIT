import streamlit as st
import openpyxl
from openpyxl import load_workbook
import gdown
import os
import zipfile
import re
import xml.etree.ElementTree as ET
import io
from shapely.geometry import LineString, Point
import networkx as nx
from pyproj import Geod

# Konfigurasi Halaman & Sembunyikan Menu Bawaan
st.set_page_config(
    page_title="Aplikasi BOQ Only - Smart Downloader",
    page_icon="📊",
    menu_items={
        'Get Help': None,
        'Report a bug': None,
        'About': None
    }
)

# Sembunyikan Header dan Footer Streamlit
hide_st_style = """
            <style>
            #MainMenu {visibility: hidden;}
            header {visibility: hidden;}
            footer {visibility: hidden;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

geod = Geod(ellps="WGS84")

# ================= AUTO CONFIG =================
MAX_DISTANCE = 0.005

FDT_COLUMN_MAP = {
    1: "C", 2: "D", 3: "E", 4: "F", 5: "G",
    6: "H", 7: "I", 8: "J", 9: "K", 10: "L",
}

STANDARD_ROW_MAP = {
    ("A", "FO 24C/2T"): 2, ("B", "FO 24C/2T"): 3,
    ("C", "FO 24C/2T"): 4, ("D", "FO 24C/2T"): 5,
    ("A", "FO 36C/3T"): 6, ("B", "FO 36C/3T"): 7,
    ("C", "FO 36C/3T"): 8, ("D", "FO 36C/3T"): 9,
    ("A", "FO 48C/4T"): 10, ("B", "FO 48C/4T"): 11,
    ("C", "FO 48C/4T"): 12, ("D", "FO 48C/4T"): 13,
}

STRAND_WIRE_ROW = 15

FDT_TYPE_ROW = {
    "48": 38,
    "72": 39,
    "96": 40,
    "144": 41,
}

TOTAL_FAT_ROW = {
    "A": 45,
    "B": 46,
    "C": 47,
    "D": 48,
}

POLE_ROW = {
    "7M5": 74,
    "7M4": 75,
    "7M3": 76,
    "7M25": 77,
    "9M5": 78,
    "9M4": 79,
    "LN": 80,
    "MTI": 81,
    "MR": 82,
}

NRO_SHEET_NAME = "BoQ NRO Cluster"
NRO_CLUSTER_CELL = "O3"
NRO_HP_CELL = "O5"

# ================= HELPER FUNCTIONS =================

def geodesic_length(coords):
    total = 0.0
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i]
        lon2, lat2 = coords[i + 1]
        _, _, dist = geod.inv(lon1, lat1, lon2, lat2)
        total += dist
    return total

def extract_coords_from_linestring(pm):
    ls = pm.find(".//LineString")
    if ls is None:
        return []
    coord_el = ls.find("coordinates")
    if coord_el is None or not coord_el.text:
        return []
    coords = []
    for c in coord_el.text.strip().split():
        lon, lat, *_ = map(float, c.split(","))
        coords.append((lon, lat))
    return coords

def norm_text(text):
    return (text or "").upper().replace('"', "").replace("'", "")

def has_any(text, variants):
    t = norm_text(text)
    return any(v in t for v in variants)

def is_text_7_25(text):
    return has_any(text, ["7-2.5", "7 / 2.5", "7M2.5", "7M 2.5", "7 M 2.5"])

def is_text_7_3(text):
    return has_any(text, ["7-3", "7 / 3", "7M3", "7M 3", "7 M 3"])

def is_text_7_4(text):
    return has_any(text, ["7-4", "7 / 4", "7M4", "7M 4", "7 M 4"])

def is_text_9_5(text):
    return has_any(text, ["9-5", "9 / 5", "9M5", "9M 5", "9 M 5"])

def is_text_9_4(text):
    return has_any(text, ["9-4", "9 / 4", "9M4", "9M 4", "9 M 4"])

def extract_line_code(line_name):
    m = re.search(r"LINE\s+([A-Z0-9]+)", line_name.upper())
    if m:
        return m.group(1)
    return line_name.strip().upper()

def extract_fdt_index_from_line_name(line_name, total_fdt=1):
    m = re.search(r"FDT\s*(\d+)", line_name.upper())
    if m:
        return int(m.group(1))
    if total_fdt == 1:
        return 1
    return None

def get_line_folders(root):
    result = []
    for folder in root.findall(".//Folder"):
        name = folder.findtext("name", "").strip()
        if name.upper().startswith("LINE "):
            result.append((name, folder))
    return result

def auto_detect_fdt_codes(root):
    codes = []
    for folder in root.findall(".//Folder"):
        fname = folder.findtext("name", "").strip().upper()
        if fname != "FDT":
            continue
        for pm in folder.findall("Placemark"):
            if pm.find(".//Point") is None:
                continue
            name = pm.findtext("name", "").strip()
            if name:
                codes.append(name)
    unique = []
    for c in codes:
        if c not in unique:
            unique.append(c)
    return unique

def build_graph_from_line_folder(line_folder):
    G = nx.Graph()
    start_node = None
    for folder in line_folder.findall(".//Folder"):
        fname = folder.findtext("name", "").upper()
        if "DISTRIBUTION CABLE" in fname or "SLING WIRE" in fname:
            for pm in folder.findall("Placemark"):
                coords = extract_coords_from_linestring(pm)
                if len(coords) < 2:
                    continue
                for i in range(len(coords) - 1):
                    a = coords[i]
                    b = coords[i + 1]
                    seg = LineString([a, b])
                    G.add_edge(a, b, geometry=seg)
                if start_node is None:
                    start_node = coords[0]
    return G, start_node

def build_virtual_line(G, start_node):
    ordered_nodes = list(nx.dfs_preorder_nodes(G, start_node))
    path_coords = []
    for i in range(len(ordered_nodes) - 1):
        a = ordered_nodes[i]
        b = ordered_nodes[i + 1]
        edge = G.get_edge_data(a, b)
        if not edge:
            continue
        coords = list(edge["geometry"].coords)
        if coords[0] != a:
            coords.reverse()
        if path_coords and path_coords[-1] == coords[0]:
            path_coords.extend(coords[1:])
        else:
            path_coords.extend(coords)
    return LineString(path_coords), path_coords

def collect_objects_from_line_folder(line_folder, virtual_line):
    all_objects = []
    for folder in line_folder.findall(".//Folder"):
        folder_name = folder.findtext("name", "")
        fname = folder_name.upper()
        for pm in folder.findall("Placemark"):
            pt = pm.find(".//Point")
            if pt is None:
                continue
            coord_el = pt.find(".//coordinates")
            if coord_el is None or not coord_el.text:
                continue
            lon, lat, *_ = map(float, coord_el.text.strip().split(","))
            point = Point(lon, lat)
            if virtual_line.distance(point) > MAX_DISTANCE:
                continue
            chain = virtual_line.project(point)
            if "NEW POLE" in fname:
                obj = "NEW"
            elif "EXISTING POLE EMR" in fname:
                obj = "EMR"
            elif "EXISTING POLE PARTNER" in fname:
                obj = "PARTNER"
            elif "FAT" in fname and "BOUNDARY" not in fname:
                obj = "FAT"
            else:
                continue
            all_objects.append((chain, pm, obj, folder_name))
    all_objects.sort(key=lambda x: x[0])
    return all_objects

def get_partner_prefix(old_name):
    old = old_name.upper().strip()
    if any(k in old for k in ["FM", "FIRSTMEDIA", "LN", "LINKNET"]):
        return "EXT.LN"
    return "EXT.MTI"

def get_cluster_name_from_file(filename):
    base = re.sub(r"\.(kmz|kml)$", "", filename, flags=re.IGNORECASE)
    base = re.sub(r"\s*\(\d+\)$", "", base).strip()
    if " - " in base:
        return base.split(" - ", 1)[1].strip()
    return re.sub(r"^TGR\d+\s*-?\s*", "", base, flags=re.IGNORECASE).strip()

def count_hp_cover(root):
    count = 0
    counted_ids = set()
    for folder in root.findall(".//Folder"):
        folder_name = folder.findtext("name", "") or ""
        folder_upper = folder_name.upper()
        is_hp_folder = (
            "HP COVER" in folder_upper
            or "HP COVERAGE" in folder_upper
            or folder_upper.strip() in ["HP"]
        )
        for pm in folder.findall(".//Placemark"):
            pm_id = id(pm)
            name = pm.findtext("name", "") or ""
            desc = pm.findtext("description", "") or ""
            text_upper = f"{folder_name} {name} {desc}".upper()
            has_point = pm.find(".//Point") is not None
            if is_hp_folder and has_point:
                count += 1
                counted_ids.add(pm_id)
            elif pm_id not in counted_ids and has_point and (
                "HP COVER" in text_upper
                or "HP COVERAGE" in text_upper
                or "HOMEPASS" in text_upper
            ):
                count += 1
                counted_ids.add(pm_id)
    return count

def boq_put(ws, fdt_index, row, value):
    col = FDT_COLUMN_MAP.get(fdt_index)
    if not col:
        return
    if value in (None, "", 0):
        ws[f"{col}{row}"] = None
    else:
        ws[f"{col}{row}"] = value


# ================= UI STREAMLIT =================

st.title("Aplikasi BOQ Only - Smart Downloader")
st.write("Target Sheet: **BoM CLUSTER AE**")

# Download template dari Google Drive di awal
FILE_ID = "1bOWQbhFRuOINRlL6truxY3b-UKs6vWBm"
boq_path = "BoQ_AE_Template.xlsx"

@st.cache_resource
def download_template():
    if os.path.exists(boq_path):
        os.remove(boq_path)
    url_drive = f"https://drive.google.com/uc?id={FILE_ID}"
    try:
        gdown.download(url_drive, boq_path, quiet=True)
    except Exception:
        pass
    if not os.path.exists(boq_path) or not zipfile.is_zipfile(boq_path):
        if os.path.exists(boq_path):
            os.remove(boq_path)
        url_export = f"https://docs.google.com/spreadsheets/d/{FILE_ID}/export?format=xlsx"
        try:
            gdown.download(url_export, boq_path, quiet=True)
        except Exception:
            pass

download_template()

if not os.path.exists(boq_path) or not zipfile.is_zipfile(boq_path):
    st.error("Gagal mendownload template BOQ dari Google Drive. Pastikan link Google Drive sudah diatur public ('Anyone with the link').")
else:
    st.success("Template BOQ berhasil dimuat!")

uploaded_kmz = st.file_uploader("Upload file As-plan drawing (.kmz / .kml):", type=['kmz', 'kml'])

if uploaded_kmz is not None:
    if st.button("Proses Data BOQ"):
        with st.spinner("Sedang memproses data drawing..."):
            try:
                # Simpan file yang diupload sementara
                kmz_path = uploaded_kmz.name
                with open(kmz_path, "wb") as f:
                    f.write(uploaded_kmz.getbuffer())

                # Load KML/KMZ
                if kmz_path.lower().endswith(".kmz"):
                    with zipfile.ZipFile(kmz_path, "r") as z:
                        kml_file = [f for f in z.namelist() if f.endswith(".kml")][0]
                        raw = z.read(kml_file).decode("utf-8", "ignore")
                else:
                    raw = open(kmz_path, encoding="utf-8").read()

                raw = re.sub(r'\s+xmlns(:\w+)?="[^"]+"', "", raw)
                raw = re.sub(r"<(/?)(\w+):", r"<\1", raw)
                root = ET.fromstring(raw)

                FDT_CODES = auto_detect_fdt_codes(root)
                line_folders = get_line_folders(root)
                cluster_name = get_cluster_name_from_file(kmz_path)
                hp_cover_total = count_hp_cover(root)

                st.write(f"**Cluster:** {cluster_name}")
                st.write(f"**HP Cover Total:** {hp_cover_total}")

                fat_count_by_fdt = {}
                boq_standard_odn = {}
                boq_strand_wire = {}
                boq_total_fat_line = {}
                boq_pole = {}

                for i in range(1, 11):
                    boq_strand_wire[i] = []
                    boq_pole[i] = {
                        "7M5": 0, "7M4": 0, "7M3": 0, "7M25": 0,
                        "9M5": 0, "9M4": 0, "LN": 0, "MTI": 0, "MR": 0,
                    }

                for line_name, line_folder in line_folders:
                    CODE_FAT = extract_line_code(line_name)
                    line_letter = CODE_FAT[0].upper()
                    fdt_index = extract_fdt_index_from_line_name(line_name, len(FDT_CODES))
                    if fdt_index is None:
                        fdt_index = 1

                    fat_count_by_fdt.setdefault(fdt_index, 0)
                    G, start_node = build_graph_from_line_folder(line_folder)
                    if start_node is None or len(G.nodes) == 0:
                        continue

                    virtual_line, path_coords = build_virtual_line(G, start_node)
                    if len(path_coords) < 2:
                        continue

                    route_length_report = round(geodesic_length(path_coords))
                    all_objects = collect_objects_from_line_folder(line_folder, virtual_line)

                    fat_count = 0
                    sw_lengths = []

                    for chain, pm, obj, folder_name in all_objects:
                        if obj == "NEW":
                            pole_text = f"{pm.findtext('name','')} {pm.findtext('description','')} {folder_name}"
                            if is_text_7_25(pole_text):
                                boq_pole[fdt_index]["7M25"] += 1
                            elif is_text_7_3(pole_text):
                                boq_pole[fdt_index]["7M3"] += 1
                            elif is_text_7_4(pole_text):
                                boq_pole[fdt_index]["7M4"] += 1
                            elif is_text_9_5(pole_text):
                                boq_pole[fdt_index]["9M5"] += 1
                            elif is_text_9_4(pole_text):
                                boq_pole[fdt_index]["9M4"] += 1
                            else:
                                boq_pole[fdt_index]["7M5"] += 1
                        elif obj == "EMR":
                            boq_pole[fdt_index]["MR"] += 1
                        elif obj == "PARTNER":
                            old = pm.findtext("name", "").strip()
                            prefix = get_partner_prefix(old)
                            if prefix == "EXT.LN":
                                boq_pole[fdt_index]["LN"] += 1
                            else:
                                boq_pole[fdt_index]["MTI"] += 1
                        elif obj == "FAT":
                            fat_count += 1

                    fat_count_by_fdt[fdt_index] += fat_count

                    for folder in line_folder.findall(".//Folder"):
                        fname = folder.findtext("name", "").upper()
                        if "SLING WIRE" not in fname:
                            continue
                        for pm in folder.findall("Placemark"):
                            coords = extract_coords_from_linestring(pm)
                            if len(coords) < 2:
                                continue
                            line_length_round = round(geodesic_length(coords))
                            sw_lengths.append(line_length_round)

                    boq_strand_wire[fdt_index].extend(sw_lengths)

                    if fat_count <= 10:
                        cable_type = "FO 24C/2T"
                    elif fat_count <= 15:
                        cable_type = "FO 36C/3T"
                    else:
                        cable_type = "FO 48C/4T"

                    boq_standard_odn[(fdt_index, line_letter, cable_type)] = route_length_report
                    boq_total_fat_line[(fdt_index, line_letter)] = fat_count

                # Export Excel
                wb = load_workbook(boq_path)
                if "BoM CLUSTER AE" not in wb.sheetnames:
                    st.error("Sheet 'BoM CLUSTER AE' tidak ditemukan pada file Excel!")
                else:
                    ws = wb["BoM CLUSTER AE"]
                    rows_to_clear = (
                        list(range(2, 14))
                        + [STRAND_WIRE_ROW]
                        + list(range(38, 42))
                        + list(range(45, 49))
                        + list(range(74, 83))
                    )

                    for fdt_idx, col in FDT_COLUMN_MAP.items():
                        for row in rows_to_clear:
                            ws[f"{col}{row}"] = None

                    for (fdt_index, line_letter, cable_type), value in boq_standard_odn.items():
                        row = STANDARD_ROW_MAP.get((line_letter, cable_type))
                        if row:
                            boq_put(ws, fdt_index, row, value)

                    for fdt_index, lengths in boq_strand_wire.items():
                        col = FDT_COLUMN_MAP.get(fdt_index)
                        if not col:
                            continue
                        if lengths:
                            ws[f"{col}{STRAND_WIRE_ROW}"] = "=" + "+".join(str(x) for x in lengths)
                        else:
                            ws[f"{col}{STRAND_WIRE_ROW}"] = None

                    for fdt_index, total_fat in fat_count_by_fdt.items():
                        col = FDT_COLUMN_MAP.get(fdt_index)
                        if not col:
                            continue
                        if total_fat <= 20:
                            ws[f"{col}{FDT_TYPE_ROW['48']}"] = 1
                        elif total_fat <= 30:
                            ws[f"{col}{FDT_TYPE_ROW['72']}"] = 1
                        elif total_fat <= 40:
                            ws[f"{col}{FDT_TYPE_ROW['96']}"] = 1
                        else:
                            ws[f"{col}{FDT_TYPE_ROW['144']}"] = 1

                    for (fdt_index, line_letter), value in boq_total_fat_line.items():
                        row = TOTAL_FAT_ROW.get(line_letter)
                        if row:
                            boq_put(ws, fdt_index, row, value)

                    for fdt_index, data in boq_pole.items():
                        for key, row in POLE_ROW.items():
                            boq_put(ws, fdt_index, row, data.get(key, 0))

                    if NRO_SHEET_NAME in wb.sheetnames:
                        ws_nro = wb[NRO_SHEET_NAME]
                        ws_nro[NRO_CLUSTER_CELL] = cluster_name
                        ws_nro[NRO_HP_CELL] = hp_cover_total if hp_cover_total > 0 else None

                    output = io.BytesIO()
                    wb.save(output)
                    
                    st.success("Proses Berhasil!")
                    st.download_button(
                        label="Download Hasil BoQ_AE_HASIL.xlsx",
                        data=output.getvalue(),
                        file_name="BoQ_AE_HASIL.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
            except Exception as e:
                st.error(f"Terjadi kesalahan saat memproses data: {e}")
