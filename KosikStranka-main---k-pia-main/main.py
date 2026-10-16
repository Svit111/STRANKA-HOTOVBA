import os
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Načítanie adresy databázy z Render prostredia (Internal alebo External URL)
DATABASE_URL = os.environ.get("DATABASE_URL")

# Pomocná funkcia na vytvorenie pripojenia k Postgresu
def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("Chýba premenná prostredia DATABASE_URL!")
    # Používame sslmode='require', pretože Render Postgres vyžaduje šifrované pripojenie
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# Inicializácia databázy v PostgreSQL
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # SERIAL a TEXT PRIMARY KEY sú v Postgres plne podporované
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS produkty (
            nazov TEXT PRIMARY KEY,
            cena REAL NOT NULL,
            kategoria TEXT NOT NULL,
            sklad INTEGER NOT NULL
        )
    """)
    
    # Skontrolujeme, či je databáza prázdna
    cursor.execute("SELECT COUNT(*) FROM produkty")
    if cursor.fetchone()[0] == 0:
        uvodne_produkty = [
            ("jablko", 1.0, "ovocie", 5),
            ("banan", 2.0, "ovocie", 3),
            ("hruska", 3.0, "ovocie", 4),
            ("rajcina", 2.0, "zelenina", 2),
            ("melon", 3.0, "zelenina", 1),
            ("pes", 10.0, "zviera", 1),
            ("macka", 20.0, "zviera", 2)
        ]
        # Pozor: SQLite používa '?', PostgreSQL vyžaduje '%s'
        cursor.executemany(
            "INSERT INTO produkty (nazov, cena, kategoria, sklad) VALUES (%s, %s, %s, %s)",
            uvodne_produkty
        )
    conn.commit()
    cursor.close()
    conn.close()

# Spustíme vytvorenie DB pri štarte (ak už tabuľka existuje, nič sa neprepíše)
if DATABASE_URL:
    init_db()

kosicek = []
posledna_zmena = {"produkt": None, "verzia": 0}

def vsetky_produkty():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT nazov, cena, kategoria, sklad FROM produkty")
    riadky = cursor.fetchall()
    cursor.close()
    conn.close()

    produkty = {}
    for nazov, cena, kategoria, sklad in riadky:
        produkty[nazov] = {
            "nazov": nazov,
            "cena": cena,
            "kategoria": kategoria,
            "sklad": sklad
        }
    return produkty

@app.get("/")
def domov(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/admin")
def admin_stranka(request: Request):
    return templates.TemplateResponse(request=request, name="admin.html")

@app.get("/api/products")
def produkty():
    return vsetky_produkty()

class ProduktRequest(BaseModel):
    nazov: str

@app.post("/api/cart/add")
def pridat_do_kosika(data: ProduktRequest):
    nazov = data.nazov
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Zmena zástupného znaku na %s
    cursor.execute("SELECT sklad FROM produkty WHERE nazov = %s", (nazov,))
    riadok = cursor.fetchone()

    if riadok is None:
        cursor.close()
        conn.close()
        return {"success": False, "message": "Produkt neexistuje."}

    sklad = riadok[0]
    if sklad <= 0:
        cursor.close()
        conn.close()
        return {"success": False, "message": "Produkt je vypredaný."}

    # Úprava UPDATE príkazu so znakom %s
    cursor.execute("UPDATE produkty SET sklad = sklad - 1 WHERE nazov = %s", (nazov,))
    conn.commit()
    cursor.close()
    conn.close()

    kosicek.append(nazov)
    return {"success": True, "message": f"{nazov} bol pridaný do košíka."}

@app.get("/api/cart")
def zobrazit_kosik():
    produkty = vsetky_produkty()
    zoznam = []
    cena = 0

    for nazov in kosicek:
        if nazov in produkty:
            produkt = produkty[nazov]
            zoznam.append({
                "nazov": nazov,
                "cena": produkt["cena"],
                "kategoria": produkt["kategoria"]
            })
            cena += produkt["cena"]

    return {"kosicek": zoznam, "cena": cena}

class KuponRequest(BaseModel):
    kupon: str

@app.post("/api/coupon")
def pouzit_kupon(data: KuponRequest):
    produkty = vsetky_produkty()
    cena = sum(produkty[nazov]["cena"] for nazov in kosicek if nazov in produkty)

    if data.kupon == "Kupon1":
        nova_cena = cena * 0.8
        return {"success": True, "message": "Zľava 20 %", "povodna_cena": cena, "cena": nova_cena}
    elif data.kupon == "Kupon2":
        nova_cena = cena * 0.1
        return {"success": True, "message": "Zľava 90 %", "povodna_cena": cena, "cena": nova_cena}
    else:
        return {"success": False, "message": "Kupón neexistuje.", "cena": cena}

class RestockRequest(BaseModel):
    nazov: str
    pocet: int

@app.post("/api/admin/restock")
def doplnit_sklad(data: RestockRequest):
    global posledna_zmena
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT sklad FROM produkty WHERE nazov = %s", (data.nazov,))
    if cursor.fetchone() is not None:
        cursor.execute("UPDATE produkty SET sklad = sklad + %s WHERE nazov = %s", (data.pocet, data.nazov))
        conn.commit()
        cursor.close()
        conn.close()

        posledna_zmena = {"produkt": data.nazov, "verzia": posledna_zmena["verzia"] + 1}
        return {
            "success": True,
            "message": f"Sklad pre {data.nazov} úspešne navýšený o {data.pocet} ks."
        }
    
    cursor.close()
    conn.close()
    return {"success": False, "message": "Produkt sa nenašiel."}

class NovyProduktRequest(BaseModel):
    nazov: str
    cena: float
    kategoria: str
    sklad: int

@app.post("/api/admin/add_product")
def pridat_novy_produkt(data: NovyProduktRequest):
    global posledna_zmena
    nazov = data.nazov.strip().lower()
    if not nazov:
        return {"success": False, "message": "Názov produktu nemôže byť prázdny."}

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Prepísané na %s a chytanie Postgres výnimky (psycopg2.IntegrityError)
        cursor.execute(
            "INSERT INTO produkty (nazov, cena, kategoria, sklad) VALUES (%s, %s, %s, %s)",
            (nazov, data.cena, data.kategoria, data.sklad)
        )
        conn.commit()
        posledna_zmena = {"produkt": nazov, "verzia": posledna_zmena["verzia"] + 1}
        return {"success": True, "message": f"Nový produkt '{nazov}' bol pridaný do databázy!"}
    except psycopg2.IntegrityError:
        conn.rollback() # Pri Postgrese je nutné po neúspešnej operácii rollbacknúť transakciu
        return {"success": False, "message": "Produkt s týmto názvom už v databáze existuje."}
    finally:
        cursor.close()
        conn.close()

@app.get("/api/updates")
def skontrolovat_aktualizacie():
    return posledna_zmena
