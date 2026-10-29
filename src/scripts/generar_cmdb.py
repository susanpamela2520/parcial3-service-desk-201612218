"""Generador reproducible de dispositivos sintéticos para la CMDB.

Uso:
  python -m scripts.generar_cmdb [--semilla 2026] [--cantidad 5000]
  python -m scripts.generar_cmdb --semilla 2026 --borrar      # borrado EXPLÍCITO de ese lote

- Misma semilla => mismos códigos (CI-S<semilla>-<n>). Se inserta con ON CONFLICT DO NOTHING:
  repetir la carga no duplica; el conjunto queda identificado por lote = 'SEMILLA-<semilla>'.
- Requiere datos de referencia existentes: clases, ubicaciones, usuarios activos y servicios.
- No usa datos personales reales: nombres, series e IP son sintéticos. Solo las clases que lo
  requieren reciben IP (rango privado 10.0.0.0/8, única por dispositivo).
- Código de salida: 0 si el lote tiene >= cantidad dispositivos, 1 si no.
"""
import argparse
import json
import random
import sys
from datetime import date, timedelta

from sqlalchemy import text

from app.db import SessionLocal

PESOS = {"HW-SRV": 6, "HW-EST": 30, "HW-RED": 8, "HW-ALM": 3, "HW-IMP": 7, "HW-CAM": 8, "HW-UPS": 5,
         "SW-APP": 10, "SW-BD": 4, "SW-SO": 8, "SW-LIC": 6, "MED-INS": 2, "MED-RES": 3}
PREFIJO = {"HW-SRV": "SRV", "HW-EST": "PC", "HW-RED": "NET", "HW-ALM": "SAN", "HW-IMP": "PRN", "HW-CAM": "CAM",
           "HW-UPS": "UPS", "SW-APP": "APP", "SW-BD": "DB", "SW-SO": "OS", "SW-LIC": "LIC", "MED-INS": "MEDIA",
           "MED-RES": "BKP"}
UBIC_HW_CENTRAL = ("SALA-SRV-1", "SALA-SRV-2", "NUBE")


def atributos(clase: str, i: int, r: random.Random) -> dict:
    ip = f"10.{(i >> 16) & 255}.{(i >> 8) & 255}.{i & 255 or 1}"
    a = {
        "HW-SRV": {"ip": ip, "cpu_nucleos": r.choice([8, 16, 32, 64]), "ram_gb": r.choice([32, 64, 128, 256]),
                   "sistema_operativo": r.choice(["Linux", "Windows Server"])},
        "HW-EST": {"ip": ip, "ram_gb": r.choice([8, 16, 32]), "sistema_operativo": r.choice(["Windows 11", "Linux", "macOS"])},
        "HW-RED": {"ip": ip, "puertos": r.choice([8, 24, 48]), "tipo_equipo": r.choice(["Switch", "Router", "Access Point", "Firewall"])},
        "HW-ALM": {"ip": ip, "capacidad_tb": r.choice([10, 20, 50, 100]), "raid": r.choice(["RAID 5", "RAID 6", "RAID 10"])},
        "HW-IMP": {"tipo_impresion": r.choice(["Láser", "Inyección", "Térmica"]), "conexion": r.choice(["USB", "Red"])},
        "HW-CAM": {"ip": ip, "resolucion": r.choice(["1080p", "4K"])},
        "HW-UPS": {"capacidad_va": r.choice([1000, 1500, 3000, 6000]), "autonomia_min": r.choice([10, 15, 30, 60])},
        "SW-APP": {"lenguaje": r.choice(["Java", "Python", ".NET", "Node.js"]), "url": f"https://app{i}.demo.local"},
        "SW-BD": {"motor": r.choice(["PostgreSQL", "SQL Server", "MySQL", "Oracle"]), "tamano_gb": r.choice([5, 20, 100, 500])},
        "SW-SO": {"familia": r.choice(["Linux", "Windows"]), "edicion": r.choice(["Standard", "Enterprise", "LTS"])},
        "SW-LIC": {"proveedor": r.choice(["Proveedor A", "Proveedor B", "Proveedor C"]), "usuarios": r.choice([10, 50, 100])},
        "MED-INS": {"formato": r.choice(["ISO", "USB"]), "checksum": f"{r.getrandbits(64):016x}"},
        "MED-RES": {"periodicidad": r.choice(["Diaria", "Semanal", "Mensual"]), "retencion_dias": r.choice([30, 90, 365])},
    }[clase]
    return a


def generar(db, semilla: int, cantidad: int) -> dict:
    r = random.Random(semilla)
    lote = f"SEMILLA-{semilla}"
    clases = {c: i for c, i in db.execute(text("SELECT codigo, id FROM ci_clase WHERE codigo = ANY(:c) AND activo"),
                                          {"c": list(PESOS)})}
    ubic = {c: i for c, i in db.execute(text("SELECT codigo, id FROM ubicacion WHERE activo ORDER BY codigo"))}
    usuarios = [i for (i,) in db.execute(text("SELECT id FROM usuario WHERE activo ORDER BY id"))]
    servicios = [i for (i,) in db.execute(text("SELECT id FROM servicio_n2 WHERE NOT dado_de_baja ORDER BY codigo"))]
    faltan = [n for n, v in (("clases", len(clases) == len(PESOS)), ("ubicaciones", ubic), ("usuarios", usuarios),
                             ("servicios", servicios)) if not v]
    if faltan:
        raise SystemExit(f"[cmdb] faltan datos de referencia: {', '.join(faltan)}. Ejecute migraciones, "
                         "import_excel y seed_demo antes del generador.")
    codigos_clase = sorted(PESOS)
    pesos = [PESOS[c] for c in codigos_clase]
    filas, plan = [], []
    for i in range(1, cantidad + 1):
        clase = r.choices(codigos_clase, pesos)[0]
        codigo = f"CI-S{semilla}-{i:05d}"
        hw = clase.startswith("HW-")
        u_cod = r.choice(UBIC_HW_CENTRAL) if clase in ("HW-SRV", "HW-ALM", "SW-BD") else r.choice(sorted(ubic))
        es_sw = clase.startswith("SW-")
        filas.append({
            "codigo": codigo, "nombre": f"{PREFIJO[clase]}-{semilla % 1000:03d}-{i:05d}", "clase_id": clases[clase],
            "descripcion": f"Dispositivo sintético {clase} #{i}",
            "numero_serie": f"SN{r.getrandbits(40):010X}" if hw else None,
            "version": f"v{r.randint(1, 9)}.{r.randint(0, 9)}",
            "fecha_adquisicion": date(2018, 1, 1) + timedelta(days=r.randint(0, 2900)),
            "estado_actual": r.choices(["Activo", "Inactivo", "En mantenimiento", "Retirado"], [85, 5, 7, 3])[0],
            "ubicacion_id": ubic[u_cod], "responsable_id": r.choice(usuarios),
            "nivel_seguridad": r.choice(["Alto", "Medio", "Bajo"]), "cumplimiento": r.choices(["Cumple", "No cumple"], [9, 1])[0],
            "estado_configuracion": r.choices(["Aprobado", "Pendiente", "Rechazado"], [8, 2, 1])[0],
            "numero_licencia": f"LIC-{r.getrandbits(24):06X}" if es_sw else None,
            "fecha_vencimiento": (date(2026, 1, 1) + timedelta(days=r.randint(0, 1000))) if es_sw else None,
            "atributos": json.dumps(atributos(clase, i, r)), "lote": lote})
        plan.append((codigo, clase, r.sample(servicios, k=r.choice([1, 1, 2]))))
    antes = db.execute(text("SELECT count(*) FROM ci WHERE lote=:l"), {"l": lote}).scalar()
    db.execute(text("""
        INSERT INTO ci (codigo, nombre, clase_id, descripcion, numero_serie, version, fecha_adquisicion, estado_actual,
            ubicacion_id, responsable_id, nivel_seguridad, cumplimiento, estado_configuracion, numero_licencia,
            fecha_vencimiento, atributos, lote)
        VALUES (:codigo, :nombre, :clase_id, :descripcion, :numero_serie, :version, :fecha_adquisicion, :estado_actual,
            :ubicacion_id, :responsable_id, :nivel_seguridad, :cumplimiento, :estado_configuracion, :numero_licencia,
            :fecha_vencimiento, CAST(:atributos AS jsonb), :lote)
        ON CONFLICT (codigo) DO NOTHING"""), filas)
    insertados = db.execute(text("SELECT count(*) FROM ci WHERE lote=:l"), {"l": lote}).scalar() - antes
    ids = {c: i for c, i in db.execute(text("SELECT codigo, id FROM ci WHERE lote=:l"), {"l": lote})}
    por_clase: dict[str, list[int]] = {}
    for codigo, clase, _ in plan:
        por_clase.setdefault(clase, []).append(ids[codigo])
    rel, cs = [], []
    for codigo, clase, srvs in plan:
        yo = ids[codigo]
        cs += [{"c": yo, "s": s} for s in srvs]
        if clase in ("SW-APP", "SW-BD", "SW-SO", "SW-LIC"):            # software alojado en un servidor
            rel.append({"o": yo, "d": r.choice(por_clase["HW-SRV"]), "t": "ALOJADO_EN"})
        if clase == "SW-APP":                                            # aplicación depende de una base de datos
            rel.append({"o": yo, "d": r.choice(por_clase["SW-BD"]), "t": "DEPENDE_DE"})
        if clase in ("HW-EST", "HW-CAM", "HW-IMP", "HW-SRV"):            # conectado a un equipo de red
            rel.append({"o": yo, "d": r.choice(por_clase["HW-RED"]), "t": "CONECTADO_A"})
        if clase in ("HW-SRV", "HW-RED"):                                 # depende de una UPS
            rel.append({"o": yo, "d": r.choice(por_clase["HW-UPS"]), "t": "DEPENDE_DE"})
        if clase == "MED-RES":
            rel.append({"o": yo, "d": r.choice(por_clase["HW-ALM"]), "t": "ALOJADO_EN"})
    if rel:
        db.execute(text("INSERT INTO ci_relacion (origen_id, destino_id, tipo, lote) VALUES (:o, :d, :t, :lote) "
                        "ON CONFLICT DO NOTHING"), [{**x, "lote": lote} for x in rel if x["o"] != x["d"]])
    db.execute(text("INSERT INTO ci_servicio (ci_id, servicio_n2_id) VALUES (:c, :s) ON CONFLICT DO NOTHING"), cs)
    db.commit()
    return {"lote": lote, "solicitados": cantidad, "insertados": insertados, "omitidos_existentes": cantidad - insertados}


def borrar(db, semilla: int) -> int:
    lote = f"SEMILLA-{semilla}"
    if db.execute(text("SELECT 1 FROM ticket_ci tc JOIN ci ON ci.id=tc.ci_id WHERE ci.lote=:l LIMIT 1"), {"l": lote}).first():
        raise SystemExit(f"[cmdb] el lote {lote} tiene CI referenciados por tickets; no se borra para no perder historial.")
    db.execute(text("DELETE FROM ci_relacion WHERE origen_id IN (SELECT id FROM ci WHERE lote=:l) "
                    "OR destino_id IN (SELECT id FROM ci WHERE lote=:l)"), {"l": lote})
    db.execute(text("DELETE FROM ci_servicio WHERE ci_id IN (SELECT id FROM ci WHERE lote=:l)"), {"l": lote})
    n = db.execute(text("DELETE FROM ci WHERE lote=:l"), {"l": lote}).rowcount
    db.commit()
    return n


def main() -> int:
    from app.services.cmdb import resumen
    ap = argparse.ArgumentParser()
    ap.add_argument("--semilla", type=int, default=2026)
    ap.add_argument("--cantidad", type=int, default=5000)
    ap.add_argument("--borrar", action="store_true", help="borra explícitamente el lote de esa semilla")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        if a.borrar:
            print(f"[cmdb] borrados {borrar(db, a.semilla)} dispositivos del lote SEMILLA-{a.semilla}")
            return 0
        r = generar(db, a.semilla, a.cantidad)
        print(f"[cmdb] {json.dumps(r, ensure_ascii=False)}")
        res = resumen(db, r["lote"])
        print(f"[cmdb] total en el lote: {res['total_dispositivos']} (códigos distintos {res['codigos_distintos']})")
        for t in res["por_tipo_de_ci"]:
            print(f"  nivel 2 {t['codigo']:4} {t['nombre']:12} {t['dispositivos']:5}")
        for c in res["por_clase"]:
            print(f"  nivel 3 {c['codigo']:8} {c['nombre']:26} {c['dispositivos']:5}")
        return 0 if res["total_dispositivos"] >= a.cantidad else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
