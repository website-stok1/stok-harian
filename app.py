from base64 import b64encode
from datetime import date, timedelta
from html import escape
from io import BytesIO
from pathlib import Path

import altair as alt
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
import pandas as pd
from PIL import Image, ImageOps
import streamlit as st

import db


PAGES = ("Dashboard", "Cek Stok", "Katalog Barang", "Supplier", "Transaksi", "Laporan")
STATUS = ("Aman", "Menipis", "Habis")
DIRECTIONS = {"in": "Masuk", "out": "Keluar"}
PHOTO_LIMIT_MB = 5
PRICE_COLUMNS = ("Harga modal (Rp)", "Fee (%)", "Harga bersih (Rp)")


def number(value):
    return f"{value:,}".replace(",", ".")


def rupiah(value):
    return f"Rp{number(value)}"


def stock_status(product):
    if product["stock"] == 0:
        return "Habis"
    if product["stock"] <= product["minimum"]:
        return "Menipis"
    return "Aman"


def heading(title, subtitle):
    st.markdown(
        f'<div class="page-head"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def metrics(*items):
    cards = []
    for label, value, help_text, warning in items:
        style = " metric-warn" if warning else ""
        cards.append(
            f'<div class="metric-card{style}"><div class="metric-label">{escape(label)}</div>'
            f'<div class="metric-value">{escape(str(value))}</div>'
            f'<div class="metric-help">{escape(help_text)}</div></div>'
        )
    st.markdown(
        f'<div class="metric-grid" style="--metrics:{len(items)}">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


def navigate(page):
    st.session_state.page = page


def product_picker(products, label, key, *, optional=False):
    by_id = {product["id"]: product for product in products}
    ids = list(by_id)
    if optional:
        ids.insert(0, None)

    def label_for(product_id):
        if product_id is None:
            return "Semua barang"
        product = by_id[product_id]
        return f'{product["sku"]} · {product["name"]}'

    return st.selectbox(label, ids, format_func=label_for, key=key)


def saved(message, key=None):
    st.session_state.flash = message
    if key:
        st.session_state[key] = st.session_state.get(key, 0) + 1
    st.rerun()


def inventory_rows(products):
    return [
        {
            "Barang": p["name"], "Stok": p["stock"], "Satuan": p["unit"],
            "SKU": p["sku"], "Kategori": p["category"], "Minimum": p["minimum"],
            "Status": stock_status(p), "Supplier": p["supplier_name"] or "—",
        }
        for p in products
    ]


def movement_rows(movements):
    return [
        {
            "Tanggal": m["movement_date"], "Jenis": DIRECTIONS[m["kind"]], "SKU": m["sku"],
            "Barang": m["product_name"], "Jumlah": m["quantity"], "Satuan": m["unit"],
            "Supplier": m["supplier_name"] or "—", "Catatan": m["note"],
        }
        for m in movements
    ]


def show_table(rows, empty):
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info(empty)


def csv_bytes(rows):
    cleaned = []
    for row in rows:
        cleaned.append({
            field: "'" + value if isinstance(value, str) and value.lstrip().startswith(
                ("=", "+", "-", "@")
            ) else value
            for field, value in row.items()
        })
    content = pd.DataFrame(cleaned).to_csv(index=False, sep=";", decimal=",", lineterminator="\r\n")
    return ("sep=;\r\n" + content).encode("utf-8-sig")


def export_buttons(rows, filename, sheet_name, *, workbook_rows=None):
    if workbook_rows is None:
        workbook_rows = rows
    csv, excel, _ = st.columns([1, 1, 3])
    csv.download_button("Unduh CSV", lambda: csv_bytes(rows), f"{filename}.csv", "text/csv",
                        icon=":material/download:", on_click="ignore")
    excel.download_button("Unduh Excel", lambda: workbook_bytes(workbook_rows, sheet_name=sheet_name),
                          f"{filename}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                          icon=":material/download:", on_click="ignore")


def workbook_bytes(rows, *, sheet_name="Transaksi"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    columns = list(rows[0])
    sheet.append(columns)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="224BC3")
    widths = {"Barang": 30, "Supplier": 24, "Catatan": 36, "Foto": 18}
    for index, column in enumerate(columns, 1):
        sheet.column_dimensions[get_column_letter(index)].width = widths.get(column, max(14, len(column) + 3))
    sheet.freeze_panes = "A2"
    for row in rows:
        sheet.append([None if column == "Foto" else row[column] for column in columns])
        for column, cell in zip(columns, sheet[sheet.max_row]):
            if isinstance(cell.value, str):
                cell.data_type = "s"
            if column == "Tanggal":
                cell.value = date.fromisoformat(cell.value)
                cell.number_format = "dd/mm/yyyy"
            elif column == "Fee (%)":
                cell.value /= 100
                cell.number_format = "0.00%"
            elif column in (*PRICE_COLUMNS, "Jumlah", "Stok", "Minimum"):
                cell.number_format = "#,##0"
            elif column == "Foto" and row[column]:
                photo = ExcelImage(BytesIO(row[column]))
                scale = min(96 / photo.width, 72 / photo.height, 1)
                photo.width = max(1, int(photo.width * scale))
                photo.height = max(1, int(photo.height * scale))
                sheet.row_dimensions[cell.row].height = 57
                sheet.add_image(photo, cell.coordinate)
    sheet.auto_filter.ref = sheet.dimensions
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def price_values(product):
    cost = product["cost"]
    fee_bps = product["fee_bps"]
    net = (cost * (db.FEE_SCALE - fee_bps) + db.FEE_SCALE // 2) // db.FEE_SCALE
    return dict(zip(PRICE_COLUMNS, (cost, fee_bps / 100, net)))


def prepare_photo(upload):
    if upload is None:
        return None
    if upload.size > PHOTO_LIMIT_MB * 1024 * 1024:
        raise db.StockError("Ukuran foto maksimal 5 MB.")
    try:
        with Image.open(upload, formats=("JPEG", "PNG", "WEBP")) as original:
            if original.width * original.height > 25_000_000:
                raise db.StockError("Resolusi foto maksimal 25 megapiksel.")
            photo = ImageOps.exif_transpose(original)
            photo.thumbnail((800, 800))
            photo = photo.convert("RGBA")
            background = Image.new("RGB", photo.size, "white")
            background.paste(photo, mask=photo.getchannel("A"))
            output = BytesIO()
            background.save(output, format="JPEG", quality=85, optimize=True)
            return output.getvalue()
    except db.StockError:
        raise
    except (Image.DecompressionBombError, OSError, ValueError) as error:
        raise db.StockError("Foto tidak dapat dibuka. Gunakan file JPG, PNG, atau WebP yang valid.") from error


@st.cache_data(max_entries=512, show_spinner=False)
def photo_thumbnail(photo):
    with Image.open(BytesIO(photo)) as image:
        image.thumbnail((180, 144))
        output = BytesIO()
        image.save(output, format="JPEG", quality=88)
        return output.getvalue()


def trend(movements, start, end, *, unit=None):
    if not movements:
        st.markdown(
            '<div class="chart-empty">Belum ada transaksi pada periode ini.</div>',
            unsafe_allow_html=True,
        )
        return
    days = (end - start).days + 1
    step = 1 if days <= 62 else 7 if days <= 400 else 30
    daily = {
        (start + timedelta(days=offset), kind): 0
        for offset in range(0, days, step) for kind in DIRECTIONS
    }
    for item in movements:
        offset = (date.fromisoformat(item["movement_date"]) - start).days
        bucket = start + timedelta(days=(offset // step) * step)
        daily[bucket, item["kind"]] += item["quantity"] if unit else 1
    label = f"Jumlah ({unit})" if unit else "Jumlah transaksi"
    frame = pd.DataFrame([
        {"Tanggal": day.isoformat(), "Jenis": DIRECTIONS[kind], "Jumlah": count}
        for (day, kind), count in daily.items()
    ])
    chart = (
        alt.Chart(frame)
        .mark_line(strokeWidth=2.7, point=days <= 14)
        .encode(
            x=alt.X("Tanggal:T", title=None, axis=alt.Axis(format="%d/%m", tickCount=5)),
            y=alt.Y("Jumlah:Q", title=label, axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("Jenis:N", title=None, scale=alt.Scale(
                domain=["Masuk", "Keluar"], range=["#3157CC", "#AB4F21"]
            ), legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("Tanggal:T", title="Awal interval", format="%d/%m/%Y"),
                     "Jenis:N", alt.Tooltip("Jumlah:Q", title=label, format=",.0f")],
        )
        .properties(height=220)
    )
    st.altair_chart(chart, width="stretch")
    if step > 1:
        st.caption(f"Data dikelompokkan per {step} hari.")


def dashboard(products):
    title, action = st.columns([4, 1], vertical_alignment="center")
    with title:
        heading("Ringkasan stok", "Pantau persediaan dan transaksi barang masuk dan keluar.")
    with action:
        st.button(
            "Catat transaksi", type="primary", icon=":material/add:", width="stretch",
            on_click=navigate, args=("Transaksi",), disabled=not products,
        )

    attention = sorted(
        [p for p in products if stock_status(p) != "Aman"], key=lambda p: (p["stock"], p["name"])
    )
    end = db.today()
    start = end - timedelta(days=29)
    recent = db.transactions(start=start, end=end)

    metrics(
        ("Barang terdaftar", number(len(products)), "SKU dalam katalog", False),
        ("Perlu restok", number(len(attention)), "Stok minimum atau habis", bool(attention)),
        ("Nilai persediaan", rupiah(sum(p["stock"] * p["cost"] for p in products)), "Berdasarkan harga modal", False),
        ("Aktivitas", number(len(recent)), "Transaksi dalam 30 hari terakhir", False),
    )
    if not products:
        st.button("Tambah barang pertama", icon=":material/add_box:", on_click=navigate, args=("Katalog Barang",))
    left, right = st.columns([1.7, 1], gap="medium")
    with left, st.container(border=True):
        st.subheader("Arus barang")
        st.caption("Jumlah transaksi dalam 30 hari terakhir")
        trend(recent, start, end)
    with right, st.container(border=True):
        st.subheader("Perlu restok")
        if attention:
            items = []
            for product in attention[:5]:
                items.append(
                    '<div class="attention-item"><div>'
                    f'<strong>{escape(product["name"])}</strong><small>{escape(product["sku"])}</small>'
                    f'</div><span>{number(product["stock"])} {escape(product["unit"])}</span></div>'
                )
            st.markdown(f'<div class="attention-list">{"".join(items)}</div>', unsafe_allow_html=True)
            st.button("Lihat cek stok", on_click=navigate, args=("Cek Stok",))
        else:
            st.caption("Belum ada barang." if not products else "Semua stok berada di atas batas minimum.")
    st.subheader("Transaksi terbaru")
    show_table(movement_rows(db.transactions(limit=6)), "Transaksi masuk dan keluar akan tampil di sini.")


def check_stock(products):
    heading("Cek stok", "Cari barang dan lihat jumlah persediaan terkini.")
    search_column, category_column, status_column = st.columns([2, 1, 1])
    search = search_column.text_input("Cari nama atau SKU").strip().casefold()
    categories = sorted({p["category"] for p in products})
    category = category_column.selectbox("Kategori", [None, *categories],
                                         format_func=lambda value: value or "Semua kategori")
    status = status_column.selectbox("Status stok", [None, *STATUS],
                                     format_func=lambda value: value or "Semua status")

    matching = [
        p for p in products
        if (search in p["name"].casefold() or search in p["sku"].casefold())
        and (category is None or p["category"] == category)
        and (status is None or stock_status(p) == status)
    ]
    rows = inventory_rows(matching)
    if not rows:
        st.info("Belum ada barang yang sesuai.")
        return

    photos = db.product_photos([product["id"] for product in matching if product["has_photo"]])
    thumbnails = {product_id: photo_thumbnail(photo) for product_id, photo in photos.items()}
    workbook_rows = [dict(row, Foto=thumbnails.get(product["id"])) for row, product in zip(rows, matching)]
    display_rows = [
        dict(row, Foto=f'data:image/jpeg;base64,{b64encode(row["Foto"]).decode("ascii")}' if row["Foto"] else "")
        for row in workbook_rows
    ]
    with st.container(key="stock_inventory"):
        st.dataframe(display_rows, hide_index=True, width="stretch", row_height=88 if thumbnails else None,
                     column_config={"Foto": st.column_config.ImageColumn("Foto", width=124)})
    export_buttons(rows, "stok-terkini", "Cek Stok", workbook_rows=workbook_rows)


def catalog(products, suppliers):
    heading("Katalog barang", "Tambah barang atau perbarui informasi barang yang sudah terdaftar.")
    mode = st.radio("Tindakan katalog", ["Tambah barang", "Edit barang"], horizontal=True,
                    label_visibility="collapsed")
    editing = mode == "Edit barang"
    if editing and not products:
        st.info("Katalog masih kosong.")
        return
    product_id = product_picker(products, "Barang yang akan diedit", "catalog_edit") if editing else None
    current = next((p for p in products if p["id"] == product_id), {})
    current_photo = db.product_photo(product_id) if current.get("has_photo") else None
    revision = st.session_state.get("catalog_revision", 0)
    supplier_names = {s["id"]: s["name"] for s in suppliers}
    supplier_options = [None, *supplier_names]
    with st.form(f"catalog_{product_id}_{revision}"):
        first, second = st.columns([1, 2])
        sku = first.text_input("SKU / Kode barang", current.get("sku", ""), placeholder="Contoh ATK-001", max_chars=40)
        name = second.text_input("Nama barang", current.get("name", ""), max_chars=120)
        first, second = st.columns(2)
        category = first.text_input("Kategori", current.get("category", ""), placeholder="Contoh Alat tulis", max_chars=60)
        unit = second.text_input("Satuan", current.get("unit", ""), placeholder="Contoh pcs, dus, atau unit", max_chars=30)
        first, second, third = st.columns(3)
        minimum = first.number_input("Stok minimum", 0, 1_000_000_000, current.get("minimum", 0))
        cost = second.number_input("Harga modal (Rp per satuan)", 0, 1_000_000_000_000, current.get("cost", 0))
        fee = third.number_input("Fee (%)", min_value=0.0, max_value=100.0,
                                 value=current.get("fee_bps", 0) / 100, step=0.01, format="%.2f",
                                 help="Persentase pengurang harga modal. Harga bersih tampil di Laporan.")
        supplier = st.selectbox("Supplier utama", supplier_options,
                                index=supplier_options.index(current.get("supplier_id")),
                                format_func=lambda value: supplier_names.get(value, "Belum ditentukan"))
        description = st.text_area("Keterangan", current.get("description", ""), max_chars=500)
        if current_photo:
            st.image(current_photo, caption="Foto tersimpan", alt="Foto barang tersimpan", width=160)
        upload = st.file_uploader("Ganti foto barang" if current_photo else "Unggah foto barang",
                                  type=["jpg", "jpeg", "png", "webp"], max_upload_size=PHOTO_LIMIT_MB)
        remove_photo = st.checkbox("Hapus foto tersimpan") if current_photo else False
        submitted = st.form_submit_button("Simpan barang", type="primary")
    if submitted:
        try:
            db.save_product(product_id, sku, name, category, unit, supplier, minimum, cost, description,
                            fee_bps=round(fee * 100), photo=prepare_photo(upload), remove_photo=remove_photo)
        except db.StockError as error:
            st.error(str(error))
        else:
            saved("Barang berhasil diperbarui." if editing else
                  "Barang berhasil ditambahkan. Catat stok awal melalui menu Transaksi.",
                  "catalog_revision")
    st.subheader("Daftar barang")
    rows = [dict(row, Foto="Ada" if product["has_photo"] else "—")
            for row, product in zip(inventory_rows(products), products)]
    show_table(rows, "Katalog masih kosong.")


def supplier_page(suppliers):
    heading("Supplier", "Kelola informasi pemasok dan kontaknya.")
    mode = st.radio("Tindakan supplier", ["Tambah supplier", "Edit supplier"], horizontal=True,
                    label_visibility="collapsed")
    editing = mode == "Edit supplier"
    if editing and not suppliers:
        st.info("Belum ada supplier.")
        return
    by_id = {s["id"]: s for s in suppliers}
    chosen = st.selectbox("Supplier yang akan diedit", list(by_id),
                          format_func=lambda value: by_id[value]["name"]) if editing else None
    current = by_id.get(chosen, {})
    revision = st.session_state.get("new_supplier", 0)
    with st.form(f"supplier_{chosen}_{revision}"):
        first, second = st.columns(2)
        name = first.text_input("Nama supplier", current.get("name", ""), max_chars=120)
        contact = second.text_input("Nama kontak", current.get("contact", ""), max_chars=120)
        first, second = st.columns(2)
        phone = first.text_input("Nomor telepon", current.get("phone", ""), max_chars=50)
        email = second.text_input("Email", current.get("email", ""), max_chars=150)
        address = st.text_area("Alamat", current.get("address", ""), max_chars=500)
        submitted = st.form_submit_button("Simpan supplier", type="primary")
    if submitted:
        try:
            db.save_supplier(chosen, name, contact, phone, email, address)
        except db.StockError as error:
            st.error(str(error))
        else:
            saved("Supplier berhasil disimpan.", None if editing else "new_supplier")
    st.subheader("Daftar supplier")
    rows = [
        {"Supplier": s["name"], "Kontak": s["contact"], "Telepon": s["phone"],
         "Email": s["email"], "Alamat": s["address"], "SKU terkait": s["product_count"]}
        for s in suppliers
    ]
    show_table(rows, "Belum ada supplier.")


def transaction_page(products, suppliers):
    heading("Transaksi", "Catat stok awal, penerimaan barang, atau pengeluaran barang.")
    if not products:
        st.info("Tambahkan barang di katalog sebelum mencatat transaksi.")
        st.button("Buka katalog", on_click=navigate, args=("Katalog Barang",))
        return
    first, second = st.columns([1, 2])
    with first:
        kind = st.radio("Jenis transaksi", list(DIRECTIONS), format_func=DIRECTIONS.get, horizontal=True,
                        key="transaction_kind")
    with second:
        chosen = product_picker(products, "Barang", "transaction_product")
    product = next(p for p in products if p["id"] == chosen)
    st.caption(f'Stok tersedia saat ini · {number(product["stock"])} {product["unit"]}')
    supplier_names = {s["id"]: s["name"] for s in suppliers}
    supplier_options = [None, *supplier_names]
    revision = st.session_state.get("new_movement", 0)
    with st.form(f"movement_{kind}_{chosen}_{revision}"):
        first, second = st.columns(2)
        quantity = first.number_input(f'Jumlah ({product["unit"]})', 1, 1_000_000_000, 1)
        movement_date = second.date_input("Tanggal transaksi (WIB)", db.today(), min_value=date(2000, 1, 1),
                                          max_value=db.today(), format="DD/MM/YYYY")
        supplier = None
        if kind == "in":
            supplier = st.selectbox(
                "Supplier pengirim", supplier_options,
                index=supplier_options.index(product["supplier_id"]),
                format_func=lambda value: supplier_names.get(value, "Tanpa supplier"),
            )
        note = st.text_area("Catatan", placeholder="Contoh stok awal, nomor dokumen, atau tujuan pengeluaran",
                            max_chars=500)
        submitted = st.form_submit_button("Simpan transaksi", type="primary")
    if submitted:
        try:
            db.record_movement(chosen, kind, quantity, movement_date, supplier, note)
        except db.StockError as error:
            st.error(str(error))
        else:
            saved(f"Transaksi barang {DIRECTIONS[kind].lower()} berhasil disimpan.", "new_movement")
    st.subheader("30 transaksi terakhir")
    show_table(movement_rows(db.transactions(limit=30)), "Belum ada transaksi.")


def report(products):
    heading("Laporan", "Analisis arus barang menurut periode dan unduh data transaksi.")
    filters, product_filter = st.columns([1, 1])
    end = db.today()
    selected_dates = filters.date_input(
        "Periode transaksi", (end - timedelta(days=29), end), min_value=date(2000, 1, 1),
        max_value=end, format="DD/MM/YYYY",
    )
    with product_filter:
        chosen = product_picker(products, "Filter barang", "report_product", optional=True)
    reported_products = {p["id"]: p for p in products if chosen is None or p["id"] == chosen}
    prices = {product_id: price_values(p) for product_id, p in reported_products.items()}
    st.subheader("Harga bersih per barang")
    st.caption("Harga bersih = harga modal × (100% − fee). Laporan memakai harga dan fee terakhir di katalog.")
    show_table([
        {"SKU": p["sku"], "Barang": p["name"], "Satuan": p["unit"], **prices[product_id]}
        for product_id, p in reported_products.items()
    ], "Belum ada barang dalam katalog.")
    if len(selected_dates) != 2:
        st.info("Pilih tanggal awal dan akhir untuk melihat laporan.")
        return
    start, end = selected_dates
    moves = db.transactions(start=start, end=end, product_id=chosen)
    unit = reported_products[chosen]["unit"] if chosen is not None else None
    totals = {kind: 0 for kind in DIRECTIONS}
    for movement in moves:
        totals[movement["kind"]] += movement["quantity"] if unit else 1
    label = unit or "transaksi pada periode ini"
    metrics(
        ("Barang masuk" if unit else "Transaksi masuk", number(totals["in"]), label, False),
        ("Barang keluar" if unit else "Transaksi keluar", number(totals["out"]), label, False),
        ("SKU dengan transaksi", number(len({m["product_id"] for m in moves})), "Jumlah SKU unik", False),
    )
    with st.container(border=True):
        st.subheader("Tren arus barang")
        trend(moves, start, end, unit=unit)
    st.subheader("Rincian transaksi")
    rows = movement_rows(moves)
    if rows:
        export_rows = [
            {**row, **prices[movement["product_id"]]} for row, movement in zip(rows, moves)
        ]
        export_buttons(export_rows, "laporan-transaksi", "Transaksi")
    show_table(rows, "Tidak ada transaksi untuk periode dan barang yang dipilih.")


def main():
    st.set_page_config(page_title="Stok Harian", page_icon=":material/inventory_2:", layout="wide",
                       initial_sidebar_state="auto")
    css = Path(__file__).with_name("style.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
    db.initialize()
    if st.session_state.get("page") not in PAGES:
        st.session_state.page = PAGES[0]
    with st.sidebar:
        st.markdown(
            '<div class="brand"><span class="brand-mark" aria-hidden="true"></span>Stok Harian</div>',
            unsafe_allow_html=True,
        )
        page = st.radio("Menu utama", PAGES, key="page", label_visibility="collapsed")
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))
    products = db.all_products()
    if page == "Dashboard":
        dashboard(products)
    elif page == "Cek Stok":
        check_stock(products)
    elif page == "Katalog Barang":
        catalog(products, db.all_suppliers())
    elif page == "Supplier":
        supplier_page(db.all_suppliers())
    elif page == "Transaksi":
        transaction_page(products, db.all_suppliers())
    elif page == "Laporan":
        report(products)


if __name__ == "__main__":
    main()
