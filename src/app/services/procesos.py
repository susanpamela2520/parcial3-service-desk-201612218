"""Definición de los siete flujos (máquinas de estado) del Service Desk.

Cada proceso declara sus estados, transiciones permitidas, el permiso que exige cada
transición y los campos obligatorios para entrar al estado destino. La lógica genérica
de ejecución está en services/tickets.py; aquí solo hay datos y validaciones propias.

Permisos de transición:
  atender      -> miembro de la cola con puede_atender
  aprobar      -> miembro con puede_aprobar Y rol 'aprobador' (o administrador); nunca el solicitante
  solicitante  -> el solicitante del ticket (o un agente que atiende)
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Transicion:
    desde: str
    hacia: str
    permiso: str = "atender"
    requiere: tuple = ()          # campos que deben tener valor al entrar a 'hacia'
    nombre: str = ""


@dataclass
class Flujo:
    codigo: str
    inicial: str
    estados: tuple
    transiciones: list = field(default_factory=list)
    resuelto: tuple = ()          # estados que marcan resolución (para resuelto_en)
    reapertura: str = ""          # estado al reabrir
    espera: tuple = ()            # estados que pausan el reloj

    def siguiente(self, desde: str, hacia: str) -> Transicion | None:
        for t in self.transiciones:
            if t.desde == desde and t.hacia == hacia:
                return t
        return None

    def desde(self, estado: str) -> list[Transicion]:
        return [t for t in self.transiciones if t.desde == estado]


T = Transicion
FLUJOS: dict[str, Flujo] = {}


def _f(flujo: Flujo):
    FLUJOS[flujo.codigo] = flujo


# 1. Gestión de nuevos servicios (subtipo de requisición de servicio)
_f(Flujo("NUEVO_SERVICIO", "SOLICITADO",
         ("SOLICITADO", "EN_ANALISIS", "APROBADO", "RECHAZADO", "EN_IMPLEMENTACION", "IMPLEMENTADO", "CERRADO"),
         [T("SOLICITADO", "EN_ANALISIS", requiere=("necesidad",)),
          T("EN_ANALISIS", "APROBADO", "aprobar", requiere=("viabilidad", "servicio_propuesto")),
          T("EN_ANALISIS", "RECHAZADO", "aprobar", requiere=("motivo_rechazo",)),
          T("APROBADO", "EN_IMPLEMENTACION"),
          T("EN_IMPLEMENTACION", "IMPLEMENTADO", requiere=("servicio_resultante_id",)),
          T("IMPLEMENTADO", "CERRADO"),
          T("RECHAZADO", "CERRADO")],
         resuelto=("IMPLEMENTADO", "RECHAZADO"), reapertura="EN_ANALISIS"))

# 2. Gestión de cambios
_f(Flujo("CAMBIO", "SOLICITADO",
         ("SOLICITADO", "EN_EVALUACION", "APROBADO", "RECHAZADO", "PLANIFICADO", "EN_EJECUCION",
          "EN_VALIDACION", "REVERTIDO", "CERRADO"),
         [T("SOLICITADO", "EN_EVALUACION", requiere=("ci_afectados",)),
          T("EN_EVALUACION", "APROBADO", "aprobar", requiere=("impacto_evaluado", "riesgo")),
          T("EN_EVALUACION", "RECHAZADO", "aprobar", requiere=("motivo_rechazo",)),
          T("APROBADO", "PLANIFICADO", requiere=("plan_pruebas", "plan_reversion", "fecha_programada")),
          T("PLANIFICADO", "EN_EJECUCION", requiere=("aprobado_por_id",)),
          T("EN_EJECUCION", "EN_VALIDACION", requiere=("resultado_ejecucion",)),
          T("EN_EJECUCION", "REVERTIDO", requiere=("motivo_reversion",)),
          T("EN_VALIDACION", "CERRADO", requiere=("validacion",)),
          T("REVERTIDO", "CERRADO"),
          T("RECHAZADO", "CERRADO")],
         resuelto=("EN_VALIDACION", "REVERTIDO", "RECHAZADO"), reapertura="EN_EVALUACION"))

# 3. Gestión de incidentes
_f(Flujo("INCIDENTE", "REGISTRADO",
         ("REGISTRADO", "CLASIFICADO", "EN_DIAGNOSTICO", "EN_ATENCION", "EN_ESPERA", "ESCALADO",
          "RECUPERADO", "RESUELTO", "CERRADO"),
         [T("REGISTRADO", "CLASIFICADO", requiere=("categoria_incidente", "afectacion")),
          T("CLASIFICADO", "EN_DIAGNOSTICO"),
          T("EN_DIAGNOSTICO", "EN_ATENCION", requiere=("diagnostico",)),
          T("EN_ATENCION", "EN_ESPERA", requiere=("motivo_espera",)),
          T("EN_ESPERA", "EN_ATENCION"),
          T("EN_ATENCION", "ESCALADO"),          # el escalamiento se hace con la acción 'escalar'
          T("ESCALADO", "EN_ATENCION"),
          T("EN_ATENCION", "RECUPERADO", requiere=("recuperacion",)),
          T("RECUPERADO", "RESUELTO", requiere=("solucion",)),
          T("RESUELTO", "CERRADO", "solicitante")],
         resuelto=("RESUELTO",), reapertura="EN_ATENCION", espera=("EN_ESPERA",)))

# 4. Gestión de eventos (la respuesta depende de E0–E3; ver services/tickets.py:_efecto_evento)
_f(Flujo("EVENTO", "REGISTRADO",
         ("REGISTRADO", "CLASIFICADO", "EVALUADO", "RESPONDIDO", "CERRADO"),
         [T("REGISTRADO", "CLASIFICADO", requiere=("categoria_evento",)),
          T("CLASIFICADO", "CERRADO"),            # solo válido para E0 (registro únicamente)
          T("CLASIFICADO", "EVALUADO", requiere=("evaluacion",)),
          T("EVALUADO", "CERRADO"),               # E1: se conserva la evaluación, sin respuesta operativa
          T("EVALUADO", "RESPONDIDO"),            # E2/E3: crea o vincula incidente y notifica
          T("RESPONDIDO", "CERRADO")],
         resuelto=("RESPONDIDO",), reapertura="CLASIFICADO"))

# 5. Gestión de problemas
_f(Flujo("PROBLEMA", "REGISTRADO",
         ("REGISTRADO", "EN_INVESTIGACION", "CAUSA_IDENTIFICADA", "ERROR_CONOCIDO", "SOLUCIONADO", "CERRADO"),
         [T("REGISTRADO", "EN_INVESTIGACION", requiere=("incidentes_vinculados",)),
          T("EN_INVESTIGACION", "CAUSA_IDENTIFICADA", requiere=("causa_raiz",)),
          T("CAUSA_IDENTIFICADA", "ERROR_CONOCIDO", requiere=("solucion_temporal",)),
          T("CAUSA_IDENTIFICADA", "SOLUCIONADO", requiere=("solucion_definitiva",)),
          T("ERROR_CONOCIDO", "SOLUCIONADO", requiere=("solucion_definitiva",)),
          T("SOLUCIONADO", "CERRADO")],
         resuelto=("SOLUCIONADO",), reapertura="EN_INVESTIGACION"))

# 6. Gestión de accesos (impide la autoaprobación)
_f(Flujo("ACCESO", "SOLICITADO",
         ("SOLICITADO", "APROBADO", "RECHAZADO", "PROVISIONADO", "REVOCADO", "VALIDADO", "CERRADO"),
         [T("SOLICITADO", "APROBADO", "aprobar", requiere=("beneficiario_id", "recurso", "permiso", "accion_acceso")),
          T("SOLICITADO", "RECHAZADO", "aprobar", requiere=("motivo_rechazo",)),
          T("APROBADO", "PROVISIONADO"),          # accion_acceso = PROVISION
          T("APROBADO", "REVOCADO"),              # accion_acceso = REVOCACION
          T("PROVISIONADO", "VALIDADO", requiere=("validacion",)),
          T("REVOCADO", "VALIDADO", requiere=("validacion",)),
          T("VALIDADO", "CERRADO"),
          T("RECHAZADO", "CERRADO")],
         resuelto=("VALIDADO", "RECHAZADO"), reapertura="SOLICITADO"))

# 7. Gestión de servicios de tecnología (sobre un servicio EXISTENTE del catálogo)
_f(Flujo("SERVICIO_TI", "SOLICITADO",
         ("SOLICITADO", "VALIDADO", "ASIGNADO", "EN_CUMPLIMIENTO", "CUMPLIDO", "CONFIRMADO", "CERRADO"),
         [T("SOLICITADO", "VALIDADO", requiere=("validacion",)),
          T("VALIDADO", "ASIGNADO", requiere=("responsable_id",)),
          T("ASIGNADO", "EN_CUMPLIMIENTO"),
          T("EN_CUMPLIMIENTO", "CUMPLIDO", requiere=("cumplimiento",)),
          T("CUMPLIDO", "CONFIRMADO", "solicitante"),
          T("CONFIRMADO", "CERRADO")],
         resuelto=("CUMPLIDO",), reapertura="EN_CUMPLIMIENTO"))

ESTADOS_FINALES = {"CERRADO"}


# ---------------------------------------------------------------- incidentes I0–I6 (diapositiva 16)
PRIORIDAD_INCIDENTE = {"I0": "BAJA", "I1": "MEDIA", "I2": "MEDIA", "I3": "ALTA",
                       "I4": "ALTA", "I5": "ALTA", "I6": "ALTA"}
# Agrupación visual de la diapositiva: I0 tiene redundancia; I1–I6 no (bloque combinado).
REDUNDANCIA_INCIDENTE = {"I0": True, **{f"I{i}": False for i in range(1, 7)}}
PRIORIDAD_NUM = {"ALTA": 2, "MEDIA": 3, "BAJA": 4}
LIMITE_MIN = {"I1": 3 * 60, "I2": 8 * 60, "I3": 2 * 24 * 60, "I4": 7 * 24 * 60}


def categoria_por_duracion(afectacion: str, minutos: int | None) -> str | None:
    """Regla complementaria del proyecto (NO proviene de la diapositiva).

    Los rótulos se solapan (3 h también es ≤ 8 h) y dejan sin intervalo (1 semana, 1 mes).
    Decisión: SEGURIDAD -> I6; NINGUNA -> I0; si no, la categoría con el límite menor que
    contiene la duración; más de 1 semana (incluye el hueco 1 semana–1 mes) -> I5.
    Sin duración conocida no se sugiere categoría (None).
    """
    if afectacion == "SEGURIDAD":
        return "I6"
    if afectacion == "NINGUNA":
        return "I0"
    if minutos is None:
        return None
    for cat in ("I1", "I2", "I3", "I4"):
        if minutos <= LIMITE_MIN[cat]:
            return cat
    return "I5"


# ---------------------------------------------------------------- eventos E0–E3 (diapositiva 15)
RESPUESTA_EVENTO = {
    "E0": "REGISTRO",                  # requiere únicamente registro
    "E1": "EVALUACION",                # sin respuesta operativa; se conserva la evaluación
    "E2": "INCIDENTE_Y_NOTIFICACION",  # impacto al servicio
    "E3": "INCIDENTE_Y_NOTIFICACION",  # impacto en seguridad de la información
}


# ---------------------------------------------------------------- prioridad general
MATRIZ_PRIORIDAD = {  # impacto x urgencia -> 1 crítica, 2 alta, 3 media, 4 baja
    ("ALTO", "ALTA"): 1, ("ALTO", "MEDIA"): 2, ("ALTO", "BAJA"): 3,
    ("MEDIO", "ALTA"): 2, ("MEDIO", "MEDIA"): 3, ("MEDIO", "BAJA"): 4,
    ("BAJO", "ALTA"): 3, ("BAJO", "MEDIA"): 4, ("BAJO", "BAJA"): 4,
}
