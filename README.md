# Stok Harian

Aplikasi inventaris berbahasa Indonesia berbasis Streamlit dan SQLite. Buka dari browser komputer atau HP.

## Menjalankan aplikasi

Ekstrak ZIP, buka terminal di folder `stok-ruang`, lalu jalankan dengan Python 3.12.

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.address 0.0.0.0
```

- Di komputer yang menjalankan aplikasi, buka [http://localhost:8501](http://localhost:8501).
- Di HP yang tersambung ke Wi-Fi yang sama, buka alamat IP lokal komputer tersebut dengan port 8501, misalnya `http://192.168.1.20:8501`. Komputer perlu tetap menyala dan firewall perlu mengizinkan port tersebut di jaringan lokal.
- Untuk mengaksesnya dari luar jaringan, jalankan Streamlit di server dengan penyimpanan permanen dan atur akses privat melalui VPN atau autentikasi di server. Aplikasi langsung memberikan akses edit kepada setiap pengunjung yang bisa membukanya.

## Mulai mencatat stok

1. Tambahkan pemasok di **Supplier** jika diperlukan.
2. Isi master barang di **Katalog Barang**. Foto produk bersifat opsional dan menerima JPG, PNG, atau WebP hingga 5 MB dan 25 megapiksel. Buka **Edit barang** untuk melihat, mengganti, atau menghapus foto. Gunakan satu satuan tetap per SKU.
3. Masukkan saldo awal lewat **Transaksi → Masuk** dengan catatan *Stok awal*. Catat penerimaan maupun pengeluaran berikutnya di menu yang sama.
4. Buka **Cek Stok** untuk mencari dan memfilter stok terkini. Foto tampil di kolom paling kanan. Unduhan Excel menyertakan foto, dan CSV berisi data stok sesuai filter.
5. Pantau stok minimum dan nilai persediaan di **Dashboard**. Gunakan filter periode dan barang di **Laporan** untuk menganalisis transaksi dan mengunduh CSV atau Excel. Saat memilih satu SKU, grafik menampilkan jumlah sesuai satuannya. Saat memilih semua barang, grafik menampilkan jumlah transaksi.

Masukkan fee dari 0% hingga 100% di **Katalog Barang**. **Laporan** menghitung harga bersih per satuan sebagai harga modal × (100% − fee), dibulatkan ke Rupiah terdekat. Laporan dan ekspor memakai harga modal serta fee terbaru dari katalog. CSV Cek Stok dan Laporan menggunakan titik koma, koma desimal, dan baris pertama `sep=;` untuk Excel. Jika mengimpor CSV ke aplikasi lain, lewati baris pertama dan pilih pemisah titik koma.

Stok tersedia dihitung dari barang masuk dikurangi barang keluar. Transaksi keluar ditolak jika menyebabkan stok negatif, termasuk saat memakai tanggal lampau. Nilai persediaan memakai harga modal yang tersimpan di katalog.

## Data dan cadangan

Saat pertama dijalankan, aplikasi membuat database kosong di `data/stok.sqlite3`. Cadangkan file tersebut saat aplikasi berhenti. Untuk penyimpanan di lokasi lain, set variabel lingkungan `STOK_DB_PATH` ke jalur file SQLite di disk permanen. Jalankan satu instans aplikasi untuk database tersebut.

### Memperbarui versi sebelumnya

Hentikan aplikasi dengan `Ctrl+C`, lalu cadangkan folder `data`. Ekstrak paket baru dan salin folder `data` dari aplikasi lama ke folder `stok-ruang` yang baru. Jalankan kembali kedua perintah instalasi dan start di atas dari folder baru. Aplikasi menambahkan kolom foto dan fee secara otomatis pada database lama. Fee barang lama dimulai dari 0%.
