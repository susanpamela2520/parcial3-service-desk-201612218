-- 004: jerarquía de clasificación de la CMDB, ubicaciones de referencia y FAQ inicial.
-- Ver docs/MODELO.md §3: cada fila indica su fuente. Lo que NO viene de las fuentes se marca
-- como DECISION_PROYECTO (las fuentes no definen niveles por debajo de «Tipo de CI»).

-- Nivel 1: el concepto de CI (lección de Transición, diapositiva 12)
INSERT INTO ci_clase (codigo, nombre, padre_id, nivel, denominacion, origen) VALUES
    ('CI', 'Elemento de configuración (CI)', NULL, 1, 'Elemento de configuración',
     'Transicion.pptx.pdf diap. 12 «Elemento de configuración CI»; diap. 11 «Identificación de CI (Id, Tipo)»');

-- Nivel 2: Tipo de CI (CMDB.xlsx C4 « Tipo de CI»; valores C5 y C6) + CI de medios (diap. 13, DML)
INSERT INTO ci_clase (codigo, nombre, padre_id, nivel, denominacion, origen)
SELECT x.codigo, x.nombre, (SELECT id FROM ci_clase WHERE codigo='CI'), 2, 'Tipo de CI', x.origen FROM (VALUES
    ('HW', 'Hardware', 'CMDB.xlsx hoja CMDB C5 « Hardware»'),
    ('SW', 'Software', 'CMDB.xlsx hoja CMDB C6 « Software»'),
    ('MED', 'Medios', 'Transicion.pptx.pdf diap. 13 «CI''s de medios» (DML)')) AS x(codigo, nombre, origen);

-- Nivel 3: Clase (DECISION_PROYECTO; las dos primeras se basan en ejemplos del Excel)
INSERT INTO ci_clase (codigo, nombre, padre_id, nivel, denominacion, origen, requiere_ip, atributos)
SELECT x.codigo, x.nombre, (SELECT id FROM ci_clase WHERE codigo=x.padre), 3, 'Clase', x.origen, x.ip, CAST(x.attrs AS jsonb)
FROM (VALUES
    ('HW-SRV', 'HW',  'Servidor',            'DECISION_PROYECTO basada en CMDB.xlsx B5/D5 «Servidor1 / Servidor de Aplicaciones»', TRUE,  '["ip","cpu_nucleos","ram_gb","sistema_operativo"]'),
    ('HW-EST', 'HW',  'Estación de trabajo', 'DECISION_PROYECTO', TRUE,  '["ip","ram_gb","sistema_operativo"]'),
    ('HW-RED', 'HW',  'Equipo de red',       'DECISION_PROYECTO (servicio SE.02.01 del catálogo)', TRUE, '["ip","puertos","tipo_equipo"]'),
    ('HW-ALM', 'HW',  'Almacenamiento',      'DECISION_PROYECTO', TRUE,  '["ip","capacidad_tb","raid"]'),
    ('HW-IMP', 'HW',  'Impresora',           'DECISION_PROYECTO (servicio SE.02.05 del catálogo)', FALSE, '["tipo_impresion","conexion"]'),
    ('HW-CAM', 'HW',  'Cámara de seguridad', 'DECISION_PROYECTO (servicio SE.01.03 del catálogo)', TRUE, '["ip","resolucion"]'),
    ('HW-UPS', 'HW',  'UPS / corriente regulada', 'DECISION_PROYECTO (servicio SE.01.02 del catálogo)', FALSE, '["capacidad_va","autonomia_min"]'),
    ('SW-APP', 'SW',  'Aplicación',          'DECISION_PROYECTO basada en CMDB.xlsx B6/D6 «Aplicación / Aplicación de contabilidad»', FALSE, '["lenguaje","url"]'),
    ('SW-BD',  'SW',  'Base de datos',       'DECISION_PROYECTO basada en CMDB.xlsx I5/I6 «Base de Datos1»', FALSE, '["motor","tamano_gb"]'),
    ('SW-SO',  'SW',  'Sistema operativo',   'DECISION_PROYECTO', FALSE, '["familia","edicion"]'),
    ('SW-LIC', 'SW',  'Software licenciado', 'DECISION_PROYECTO basada en CMDB.xlsx T4/U4 (licencia y vencimiento)', FALSE, '["proveedor","usuarios"]'),
    ('MED-INS','MED', 'Medio de instalación','DECISION_PROYECTO', FALSE, '["formato","checksum"]'),
    ('MED-RES','MED', 'Medio de respaldo',   'DECISION_PROYECTO', FALSE, '["periodicidad","retencion_dias"]')
) AS x(codigo, padre, nombre, origen, ip, attrs);

INSERT INTO ubicacion (codigo, nombre, tipo, origen) VALUES
    ('SALA-SRV-1', 'Sala de Servidores 1', 'SALA', 'CMDB.xlsx K5 « Sala de Servidores 1»'),
    ('SALA-SRV-2', 'Sala de Servidores 2', 'SALA', 'DECISION_PROYECTO'),
    ('ED-C-P1', 'Edificio Central — Piso 1', 'PISO', 'DECISION_PROYECTO'),
    ('ED-C-P2', 'Edificio Central — Piso 2', 'PISO', 'DECISION_PROYECTO'),
    ('ED-C-P3', 'Edificio Central — Piso 3', 'PISO', 'DECISION_PROYECTO'),
    ('ED-N-P1', 'Edificio Norte — Piso 1', 'PISO', 'DECISION_PROYECTO'),
    ('ED-N-P2', 'Edificio Norte — Piso 2', 'PISO', 'DECISION_PROYECTO'),
    ('BODEGA', 'Bodega de TI', 'BODEGA', 'DECISION_PROYECTO'),
    ('NUBE', 'Proveedor de nube', 'EXTERNO', 'DECISION_PROYECTO');

INSERT INTO kb_articulo (titulo, contenido) VALUES
    ('¿Cómo restablezco mi contraseña?', 'Nivel 0 (autoservicio): use la opción de restablecimiento del portal. Si no funciona, registre un ticket de acceso.'),
    ('No tengo internet en mi puesto', 'Verifique el cable de red y reinicie el equipo. Si persiste, registre un incidente sobre el servicio de Internet.'),
    ('¿Cómo solicito acceso a un sistema?', 'Registre un ticket del proceso Gestión de accesos indicando beneficiario, recurso y permiso. Requiere aprobación.'),
    ('¿Cuál es la diferencia entre incidente y requisición?', 'Un incidente es una interrupción o degradación; una requisición es una solicitud de un servicio existente.');
