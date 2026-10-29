-- 003: Service Desk (parcial 3) — roles múltiples, procesos, colas, tickets, bitácora y CMDB.
-- No modifica datos del parcial 2: solo amplía el modelo.

-- ---------------------------------------------------------------- roles
-- usuario.rol queda como rol principal; usuario_rol agrega roles adicionales.
ALTER TABLE usuario DROP CONSTRAINT IF EXISTS usuario_rol_check;
ALTER TABLE usuario ADD CONSTRAINT usuario_rol_check
    CHECK (rol IN ('administrador', 'consulta', 'solicitante', 'agente', 'aprobador'));

CREATE TABLE usuario_rol (
    usuario_id INTEGER     NOT NULL REFERENCES usuario(id) ON DELETE RESTRICT,
    rol        VARCHAR(20) NOT NULL CHECK (rol IN ('administrador', 'consulta', 'solicitante', 'agente', 'aprobador')),
    PRIMARY KEY (usuario_id, rol)
);

-- ---------------------------------------------------------------- procesos y niveles
-- Los 7 procesos de la tarea y su mapeo al tipo de ticket de la diapositiva 14 (lección 25).
CREATE TABLE proceso (
    codigo      VARCHAR(30)  PRIMARY KEY,
    nombre      VARCHAR(120) NOT NULL,
    tipo_ticket VARCHAR(40)  NOT NULL CHECK (tipo_ticket IN
        ('SOLICITUD_ACCESO', 'EVENTO', 'INCIDENTE', 'SOLICITUD_CAMBIO', 'REQUISICION_SERVICIO', 'PROBLEMA')),
    subtipo     VARCHAR(40),
    orden       INTEGER      NOT NULL
);

INSERT INTO proceso (codigo, nombre, tipo_ticket, subtipo, orden) VALUES
    ('NUEVO_SERVICIO', 'Gestión de nuevos servicios',          'REQUISICION_SERVICIO', 'NUEVO_SERVICIO', 1),
    ('CAMBIO',         'Gestión de cambios',                   'SOLICITUD_CAMBIO',     NULL, 2),
    ('INCIDENTE',      'Gestión de incidentes',                'INCIDENTE',            NULL, 3),
    ('EVENTO',         'Gestión de eventos',                   'EVENTO',               NULL, 4),
    ('PROBLEMA',       'Gestión de problemas',                 'PROBLEMA',             NULL, 5),
    ('ACCESO',         'Gestión de accesos',                   'SOLICITUD_ACCESO',     NULL, 6),
    ('SERVICIO_TI',    'Gestión de servicios de tecnología',   'REQUISICION_SERVICIO', 'SERVICIO_EXISTENTE', 7);

-- Esquema configurable de niveles de soporte (diapositivas 10–13: 0–4 vs 0–3; ver docs/PROCESOS.md)
CREATE TABLE nivel_soporte (
    nivel        INTEGER      PRIMARY KEY CHECK (nivel BETWEEN 0 AND 9),
    nombre       VARCHAR(80)  NOT NULL,
    equivalencia VARCHAR(200) NOT NULL,
    referencia_porcentaje VARCHAR(10),
    activo       BOOLEAN      NOT NULL DEFAULT TRUE
);
INSERT INTO nivel_soporte (nivel, nombre, equivalencia, referencia_porcentaje) VALUES
    (0, 'Autoservicio',       'Diap. 11 Nivel 0 Autoservicio = Diap. 12 Nivel 0 Autoservicio', '20 %'),
    (1, 'Help Desk',          'Diap. 11 Nivel 1 Help Desk = Diap. 12 Nivel 1 Help Desk',       '70 %'),
    (2, 'Soporte técnico / Ingenieros', 'Diap. 11 Niveles 2 (Soporte Técnico) y 3 (Ingenieros) = Diap. 13 Nivel 2 Ingenieros', '5 %'),
    (3, 'Proveedores externos', 'Diap. 11 Nivel 4 Externo = Diap. 13 Nivel 3 Proveedores',     '5 %');

-- ---------------------------------------------------------------- colas
CREATE TABLE cola (
    id              SERIAL PRIMARY KEY,
    codigo          VARCHAR(30)  NOT NULL UNIQUE,
    nombre          VARCHAR(150) NOT NULL,
    descripcion     TEXT,
    proceso_codigo  VARCHAR(30)  NOT NULL REFERENCES proceso(codigo),
    nivel_soporte   INTEGER      NOT NULL REFERENCES nivel_soporte(nivel),
    regla_orden     VARCHAR(40)  NOT NULL DEFAULT 'PRIORIDAD_ANTIGUEDAD'
                    CHECK (regla_orden IN ('PRIORIDAD_ANTIGUEDAD', 'ANTIGUEDAD')),
    objetivo_primera_atencion_min INTEGER CHECK (objetivo_primera_atencion_min > 0),
    objetivo_resolucion_min       INTEGER CHECK (objetivo_resolucion_min > 0),
    activo          BOOLEAN      NOT NULL DEFAULT TRUE,
    creado_en       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    actualizado_en  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Miembros: un usuario O un rol, con permisos separados
CREATE TABLE cola_miembro (
    id                SERIAL PRIMARY KEY,
    cola_id           INTEGER NOT NULL REFERENCES cola(id),
    usuario_id        INTEGER REFERENCES usuario(id),
    rol               VARCHAR(20) CHECK (rol IN ('administrador', 'consulta', 'solicitante', 'agente', 'aprobador')),
    puede_consultar   BOOLEAN NOT NULL DEFAULT TRUE,
    puede_atender     BOOLEAN NOT NULL DEFAULT FALSE,
    puede_transferir  BOOLEAN NOT NULL DEFAULT FALSE,
    puede_aprobar     BOOLEAN NOT NULL DEFAULT FALSE,
    puede_administrar BOOLEAN NOT NULL DEFAULT FALSE,
    activo            BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT ck_miembro_uno CHECK ((usuario_id IS NULL) <> (rol IS NULL))
);
CREATE UNIQUE INDEX uq_cola_miembro_usuario ON cola_miembro (cola_id, usuario_id) WHERE usuario_id IS NOT NULL;
CREATE UNIQUE INDEX uq_cola_miembro_rol ON cola_miembro (cola_id, rol) WHERE rol IS NOT NULL;

-- ---------------------------------------------------------------- CMDB
CREATE TABLE ci_clase (
    id           SERIAL PRIMARY KEY,
    codigo       VARCHAR(40)  NOT NULL UNIQUE,
    nombre       VARCHAR(120) NOT NULL,
    padre_id     INTEGER      REFERENCES ci_clase(id),
    nivel        INTEGER      NOT NULL,
    denominacion VARCHAR(60)  NOT NULL,      -- nombre del nivel (Tipo de CI, Clase, Subclase)
    origen       VARCHAR(200) NOT NULL,      -- celda/diapositiva fuente o 'DECISION_PROYECTO'
    requiere_ip  BOOLEAN      NOT NULL DEFAULT FALSE,
    atributos    JSONB        NOT NULL DEFAULT '[]'::jsonb,  -- atributos técnicos de la clase
    activo       BOOLEAN      NOT NULL DEFAULT TRUE,
    CONSTRAINT ck_clase_no_self CHECK (padre_id IS NULL OR padre_id <> id)
);

-- Impide ciclos en la jerarquía de clasificación
CREATE FUNCTION fn_ci_clase_sin_ciclo() RETURNS trigger AS $$
DECLARE actual INTEGER := NEW.padre_id; pasos INTEGER := 0;
BEGIN
    WHILE actual IS NOT NULL LOOP
        IF actual = NEW.id THEN
            RAISE EXCEPTION 'Ciclo en la jerarquía de clasificación' USING ERRCODE = 'check_violation';
        END IF;
        SELECT padre_id INTO actual FROM ci_clase WHERE id = actual;
        pasos := pasos + 1;
        IF pasos > 100 THEN RAISE EXCEPTION 'Jerarquía demasiado profunda'; END IF;
    END LOOP;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER tg_ci_clase_sin_ciclo BEFORE INSERT OR UPDATE OF padre_id ON ci_clase
    FOR EACH ROW EXECUTE FUNCTION fn_ci_clase_sin_ciclo();

CREATE TABLE ubicacion (
    id      SERIAL PRIMARY KEY,
    codigo  VARCHAR(40)  NOT NULL UNIQUE,
    nombre  VARCHAR(150) NOT NULL,
    tipo    VARCHAR(30)  NOT NULL,
    origen  VARCHAR(100) NOT NULL DEFAULT 'DECISION_PROYECTO',
    activo  BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE ci (
    id                   SERIAL PRIMARY KEY,
    codigo               VARCHAR(40)  NOT NULL UNIQUE,
    nombre               VARCHAR(200) NOT NULL,
    clase_id             INTEGER      NOT NULL REFERENCES ci_clase(id),
    -- columnas de CMDB.xlsx (B4:U4)
    descripcion          TEXT,
    numero_serie         VARCHAR(80),
    version              VARCHAR(40),
    fecha_adquisicion    DATE,
    estado_actual        VARCHAR(30)  NOT NULL CHECK (estado_actual IN ('Activo', 'Inactivo', 'En mantenimiento', 'Retirado')),
    ubicacion_id         INTEGER      NOT NULL REFERENCES ubicacion(id),
    responsable_id       INTEGER      NOT NULL REFERENCES usuario(id),
    responsable_original VARCHAR(150),
    fecha_cambio         DATE,
    descripcion_cambio   TEXT,
    documentacion        TEXT,
    enlaces_incidentes   TEXT,
    nivel_seguridad      VARCHAR(20)  CHECK (nivel_seguridad IN ('Alto', 'Medio', 'Bajo')),
    cumplimiento         VARCHAR(20)  CHECK (cumplimiento IN ('Cumple', 'No cumple')),
    estado_configuracion VARCHAR(20)  CHECK (estado_configuracion IN ('Aprobado', 'Pendiente', 'Rechazado')),
    numero_licencia      VARCHAR(60),
    fecha_vencimiento    DATE,
    -- agregados por el proyecto
    atributos            JSONB        NOT NULL DEFAULT '{}'::jsonb,  -- técnicos según la clase
    lote                 VARCHAR(40)  NOT NULL DEFAULT 'MANUAL',      -- 'CMDB.xlsx', 'SEMILLA-<n>' o 'MANUAL'
    activo               BOOLEAN      NOT NULL DEFAULT TRUE,
    creado_en            TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_ci_clase ON ci (clase_id);
CREATE INDEX ix_ci_lote ON ci (lote);
CREATE INDEX ix_ci_nombre ON ci (lower(nombre));

-- Relaciones técnicas (distintas de la jerarquía de clasificación)
CREATE TABLE ci_relacion_tipo (
    codigo         VARCHAR(30) PRIMARY KEY,
    nombre         VARCHAR(80) NOT NULL,
    admite_ciclos  BOOLEAN     NOT NULL,
    propaga_impacto BOOLEAN    NOT NULL,
    origen         VARCHAR(120) NOT NULL
);
INSERT INTO ci_relacion_tipo VALUES
    ('DEPENDE_DE',   'depende de',   FALSE, TRUE,  'CMDB.xlsx columna I «Relaciones»'),
    ('ALOJADO_EN',   'alojado en',   FALSE, TRUE,  'CMDB.xlsx K6 «Ubicación Física» = Servidor1'),
    ('COMPONENTE_DE','componente de',FALSE, TRUE,  'CMDB.xlsx columna J «Padres/Hijos»'),
    ('CONECTADO_A',  'conectado a',  TRUE,  FALSE, 'Enunciado §8 (relación de red, simétrica)');

CREATE TABLE ci_relacion (
    id         SERIAL PRIMARY KEY,
    origen_id  INTEGER     NOT NULL REFERENCES ci(id),
    destino_id INTEGER     NOT NULL REFERENCES ci(id),
    tipo       VARCHAR(30) NOT NULL REFERENCES ci_relacion_tipo(codigo),
    lote       VARCHAR(40) NOT NULL DEFAULT 'MANUAL',
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ci_relacion UNIQUE (origen_id, destino_id, tipo),
    CONSTRAINT ck_ci_relacion_self CHECK (origen_id <> destino_id)
);
CREATE INDEX ix_ci_rel_destino ON ci_relacion (destino_id, tipo);

CREATE TABLE ci_servicio (
    ci_id          INTEGER NOT NULL REFERENCES ci(id),
    servicio_n2_id INTEGER NOT NULL REFERENCES servicio_n2(id),
    PRIMARY KEY (ci_id, servicio_n2_id)
);
CREATE INDEX ix_ci_servicio_srv ON ci_servicio (servicio_n2_id);

-- ---------------------------------------------------------------- tickets
CREATE SEQUENCE ticket_numero_seq;

CREATE TABLE ticket (
    id               SERIAL PRIMARY KEY,
    numero           VARCHAR(20)  NOT NULL UNIQUE,
    titulo           VARCHAR(200) NOT NULL,
    descripcion      TEXT         NOT NULL,
    solicitante_id   INTEGER      NOT NULL REFERENCES usuario(id),
    creador_id       INTEGER      NOT NULL REFERENCES usuario(id),
    servicio_n2_id   INTEGER      NOT NULL REFERENCES servicio_n2(id),
    tipo_ticket      VARCHAR(40)  NOT NULL,
    proceso_codigo   VARCHAR(30)  NOT NULL REFERENCES proceso(codigo),
    cola_id          INTEGER      NOT NULL REFERENCES cola(id),
    responsable_id   INTEGER      REFERENCES usuario(id),
    estado           VARCHAR(30)  NOT NULL,
    prioridad        INTEGER      NOT NULL CHECK (prioridad BETWEEN 1 AND 4),  -- 1 crítica … 4 baja
    impacto          VARCHAR(10)  NOT NULL CHECK (impacto IN ('ALTO', 'MEDIO', 'BAJO')),
    urgencia         VARCHAR(10)  NOT NULL CHECK (urgencia IN ('ALTA', 'MEDIA', 'BAJA')),
    canal            VARCHAR(20)  NOT NULL CHECK (canal IN ('PORTAL', 'TELEFONO', 'CORREO', 'CHAT', 'MONITOREO')),
    nivel_soporte    INTEGER      NOT NULL REFERENCES nivel_soporte(nivel),
    -- datos de proceso
    categoria_evento     VARCHAR(2)  CHECK (categoria_evento IN ('E0', 'E1', 'E2', 'E3')),
    huella_evento        VARCHAR(120),
    categoria_incidente  VARCHAR(2)  CHECK (categoria_incidente IN ('I0', 'I1', 'I2', 'I3', 'I4', 'I5', 'I6')),
    afectacion           VARCHAR(20) CHECK (afectacion IN ('NINGUNA', 'DEGRADACION', 'INTERRUPCION', 'SEGURIDAD')),
    duracion_estimada_min INTEGER    CHECK (duracion_estimada_min >= 0),
    duracion_real_min     INTEGER    CHECK (duracion_real_min >= 0),
    redundancia_disponible BOOLEAN,
    beneficiario_id  INTEGER      REFERENCES usuario(id),
    aprobado_por_id  INTEGER      REFERENCES usuario(id),
    aprobado_en      TIMESTAMPTZ,
    datos            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    sin_ci_motivo    TEXT,
    reaperturas      INTEGER      NOT NULL DEFAULT 0,
    version          INTEGER      NOT NULL DEFAULT 1,
    lote             VARCHAR(40)  NOT NULL DEFAULT 'MANUAL',
    creado_en        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    actualizado_en   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    primera_atencion_en TIMESTAMPTZ,
    resuelto_en      TIMESTAMPTZ,
    cerrado_en       TIMESTAMPTZ,
    CONSTRAINT ck_acceso_no_autoaprobado CHECK (aprobado_por_id IS NULL OR aprobado_por_id <> solicitante_id OR proceso_codigo NOT IN ('ACCESO', 'CAMBIO'))
);
CREATE UNIQUE INDEX uq_ticket_huella_evento ON ticket (huella_evento) WHERE huella_evento IS NOT NULL;
CREATE INDEX ix_ticket_cola_orden ON ticket (cola_id, prioridad, creado_en, id);
CREATE INDEX ix_ticket_solicitante ON ticket (solicitante_id);
CREATE INDEX ix_ticket_estado ON ticket (estado);

CREATE TABLE ticket_ci (
    ticket_id INTEGER NOT NULL REFERENCES ticket(id),
    ci_id     INTEGER NOT NULL REFERENCES ci(id),
    PRIMARY KEY (ticket_id, ci_id)
);
CREATE INDEX ix_ticket_ci_ci ON ticket_ci (ci_id);

CREATE TABLE ticket_relacion (
    id         SERIAL PRIMARY KEY,
    origen_id  INTEGER     NOT NULL REFERENCES ticket(id),
    destino_id INTEGER     NOT NULL REFERENCES ticket(id),
    tipo       VARCHAR(30) NOT NULL CHECK (tipo IN ('RELACIONADO', 'EVENTO_GENERA_INCIDENTE', 'INCIDENTE_DE_PROBLEMA',
                                                  'CAMBIO_CORRIGE_PROBLEMA', 'DUPLICADO_DE')),
    creado_por INTEGER     NOT NULL REFERENCES usuario(id),
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ticket_relacion UNIQUE (origen_id, destino_id, tipo),
    CONSTRAINT ck_ticket_relacion_self CHECK (origen_id <> destino_id)
);

-- Bitácora inmutable: actor, fecha, valores anteriores y nuevos
CREATE TABLE ticket_bitacora (
    id        BIGSERIAL PRIMARY KEY,
    ticket_id INTEGER     NOT NULL REFERENCES ticket(id),
    actor_id  INTEGER     NOT NULL REFERENCES usuario(id),
    fecha     TIMESTAMPTZ NOT NULL DEFAULT now(),
    accion    VARCHAR(40) NOT NULL,
    anterior  JSONB,
    nuevo     JSONB,
    motivo    TEXT
);
CREATE INDEX ix_bitacora_ticket ON ticket_bitacora (ticket_id, id);
CREATE FUNCTION fn_bitacora_inmutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'La bitácora no se puede modificar ni eliminar'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER tg_bitacora_inmutable BEFORE UPDATE OR DELETE ON ticket_bitacora
    FOR EACH ROW EXECUTE FUNCTION fn_bitacora_inmutable();

CREATE TABLE ticket_comentario (
    id        SERIAL PRIMARY KEY,
    ticket_id INTEGER     NOT NULL REFERENCES ticket(id),
    autor_id  INTEGER     NOT NULL REFERENCES usuario(id),
    texto     TEXT        NOT NULL,
    interno   BOOLEAN     NOT NULL DEFAULT FALSE,
    fecha     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ticket_adjunto (
    id         SERIAL PRIMARY KEY,
    ticket_id  INTEGER      NOT NULL REFERENCES ticket(id),
    nombre     VARCHAR(200) NOT NULL,
    tipo_mime  VARCHAR(80)  NOT NULL,
    tamano     INTEGER      NOT NULL CHECK (tamano > 0 AND tamano <= 2097152),
    sha256     CHAR(64)     NOT NULL,
    contenido  BYTEA        NOT NULL,
    interno    BOOLEAN      NOT NULL DEFAULT FALSE,
    subido_por INTEGER      NOT NULL REFERENCES usuario(id),
    fecha      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Pausas del reloj (estado EN_ESPERA) para indicadores
CREATE TABLE ticket_pausa (
    id        SERIAL PRIMARY KEY,
    ticket_id INTEGER     NOT NULL REFERENCES ticket(id),
    inicio    TIMESTAMPTZ NOT NULL,
    fin       TIMESTAMPTZ,
    motivo    TEXT
);

-- Notificaciones verificables dentro de la aplicación (sin correo real)
CREATE TABLE notificacion (
    id         SERIAL PRIMARY KEY,
    usuario_id INTEGER     REFERENCES usuario(id),
    cola_id    INTEGER     REFERENCES cola(id),
    ticket_id  INTEGER     REFERENCES ticket(id),
    canal      VARCHAR(20) NOT NULL DEFAULT 'APLICACION',
    mensaje    TEXT        NOT NULL,
    fecha      TIMESTAMPTZ NOT NULL DEFAULT now(),
    leida      BOOLEAN     NOT NULL DEFAULT FALSE
);

-- Base de conocimiento / FAQ (nivel 0)
CREATE TABLE kb_articulo (
    id             SERIAL PRIMARY KEY,
    titulo         VARCHAR(200) NOT NULL,
    contenido      TEXT         NOT NULL,
    servicio_n2_id INTEGER      REFERENCES servicio_n2(id),
    activo         BOOLEAN      NOT NULL DEFAULT TRUE
);
