// Service Desk y CMDB (parcial 3). Reutiliza los utilitarios de app.js ($, esc, api, dialogo, aviso…).
// La interfaz solo oculta acciones; la autorización real la decide el servidor en cada petición.
"use strict";

const PRIORIDAD = { 1: "Crítica", 2: "Alta", 3: "Media", 4: "Baja" };
const etqEstado = (e) => `<span class="etq ${e === "CERRADO" ? "gris" : "ok"}">${esc(e)}</span>`;
const fecha = (f) => (f ? new Date(f).toLocaleString() : "—");
let refSD = null;

async function referencias() {
  if (!refSD) {
    const [procesos, colasL, n1, servicios] = await Promise.all([
      api("/procesos"), api("/colas"), api("/servicios-n1"), api("/servicios?tamano=100")]);
    refSD = { procesos: procesos.procesos, eventos: procesos.eventos, colas: colasL, n1, servicios: servicios.items };
  }
  return refSD;
}

// ------------------------------------------------------------------ portal del solicitante
async function vistaPortal() {
  const [r, kb, mios, notif] = await Promise.all([referencias(), api("/kb"), api("/tickets?mios=true&tamano=50"), api("/notificaciones")]);
  $("#contenido").innerHTML = `
    <div class="panel"><h2>Portal del solicitante</h2>
      <form id="f-nuevo" class="filtros">
        <label>Proceso <select name="proceso_codigo" required>${r.procesos.map((p) => `<option value="${p.codigo}">${esc(p.nombre)}</option>`).join("")}</select></label>
        <label>Servicio <select name="servicio_n2_id" required>${r.servicios.map((s) => `<option value="${s.id}">${esc(s.codigo)} · ${esc(s.nombre)}</option>`).join("")}</select></label>
        <label>Impacto <select name="impacto"><option>MEDIO</option><option>ALTO</option><option>BAJO</option></select></label>
        <label>Urgencia <select name="urgencia"><option>MEDIA</option><option>ALTA</option><option>BAJA</option></select></label>
        <label>Canal <select name="canal"><option>PORTAL</option><option>TELEFONO</option><option>CORREO</option><option>CHAT</option></select></label>
        <label class="ancho">Título <input name="titulo" required></label>
        <label class="ancho">Descripción <textarea name="descripcion" rows="2" required></textarea></label>
        <label>CI afectados (ids separados por coma) <input name="ci_ids" placeholder="ej. 12, 15"></label>
        <label>Motivo si no hay CI <input name="sin_ci_motivo"></label>
        <label>Beneficiario (id, solo accesos) <input name="beneficiario_id" type="number"></label>
        <button>Registrar ticket</button>
      </form><p class="error" id="n-err"></p></div>
    <div class="panel"><h3>Mis tickets</h3>${tablaTickets(mios.items)}</div>
    <div class="panel"><h3>Base de conocimiento (nivel 0 · autoservicio)</h3>
      ${kb.map((a) => `<details><summary>${esc(a.titulo)}</summary><p>${esc(a.contenido)}</p></details>`).join("")}</div>
    <div class="panel"><h3>Notificaciones</h3>
      ${notif.slice(0, 10).map((n) => `<div>${fecha(n.fecha)} · ${esc(n.mensaje)}</div>`).join("") || "Sin notificaciones"}</div>`;
  ligarTabla();
  $("#f-nuevo").addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = formDatos(e.target);
    d.ci_ids = (d.ci_ids || "").split(",").map((x) => parseInt(x)).filter((x) => x > 0);
    if (!d.ci_ids.length) delete d.ci_ids;
    try { const t = await api("/tickets", { method: "POST", body: d }); aviso(`Ticket ${t.numero} registrado`); vistaPortal(); }
    catch (err) { $("#n-err").textContent = err.message; }
  });
}

function tablaTickets(items) {
  return `<table><thead><tr><th>Número</th><th>Título</th><th>Proceso</th><th>Cola</th><th>Estado</th><th>Prioridad</th><th>Responsable</th><th>Creado</th></tr></thead>
    <tbody>${items.map((t) => `<tr class="clic" data-t="${t.id}"><td>${esc(t.numero)}</td><td>${esc(t.titulo)}</td>
      <td>${esc(t.proceso_codigo)}${t.categoria_incidente ? " " + esc(t.categoria_incidente) : ""}${t.categoria_evento ? " " + esc(t.categoria_evento) : ""}</td>
      <td>${esc(t.cola)}</td><td>${etqEstado(t.estado)}</td><td>${PRIORIDAD[t.prioridad]}</td><td>${esc(t.responsable) || "—"}</td>
      <td>${fecha(t.creado_en)}</td></tr>`).join("") || '<tr><td colspan="8">Sin tickets</td></tr>'}</tbody></table>`;
}
const ligarTabla = () => document.querySelectorAll("tr[data-t]").forEach((tr) => (tr.onclick = () => fichaTicket(tr.dataset.t)));

// ------------------------------------------------------------------ consola de agentes
const filtrosT = { pagina: 1 };
async function vistaConsola() {
  const r = await referencias();
  $("#contenido").innerHTML = `<div class="panel"><form id="ft" class="filtros">
      <label>Número <input name="numero" value="${esc(filtrosT.numero)}"></label>
      <label>Proceso <select name="proceso"><option value="">Todos</option>${r.procesos.map((p) => `<option ${filtrosT.proceso === p.codigo ? "selected" : ""}>${p.codigo}</option>`).join("")}</select></label>
      <label>Cola <select name="cola_id">${opciones(r.colas, filtrosT.cola_id, (c) => c.codigo, "Todas")}</select></label>
      <label>Estado <input name="estado" value="${esc(filtrosT.estado)}"></label>
      <label>Prioridad <select name="prioridad"><option value="">Todas</option>${[1, 2, 3, 4].map((p) => `<option value="${p}" ${filtrosT.prioridad == p ? "selected" : ""}>${PRIORIDAD[p]}</option>`).join("")}</select></label>
      <label>Servicio <select name="servicio_n2_id">${opciones(r.servicios, filtrosT.servicio_n2_id, (s) => s.codigo, "Todos")}</select></label>
      <label>Responsable (id) <input name="responsable_id" type="number" value="${esc(filtrosT.responsable_id)}"></label>
      <label>Desde <input name="desde" type="date" value="${esc(filtrosT.desde)}"></label>
      <label>Hasta <input name="hasta" type="date" value="${esc(filtrosT.hasta)}"></label>
      <label><span><input type="checkbox" name="sin_asignar" style="width:auto" ${filtrosT.sin_asignar ? "checked" : ""}> Sin asignar</span></label>
      <label><span><input type="checkbox" name="abiertos" style="width:auto" ${filtrosT.abiertos ? "checked" : ""}> Solo abiertos</span></label>
      <button>Filtrar</button></form></div>
    <div class="panel" id="tt">Cargando…</div>`;
  $("#ft").onsubmit = (e) => { e.preventDefault(); const d = formDatos(e.target); Object.assign(filtrosT, d, { sin_asignar: !!d.sin_asignar, abiertos: !!d.abiertos, pagina: 1 }); cargarConsola(); };
  cargarConsola();
}
async function cargarConsola() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filtrosT)) if (v) p.set(k, v === true ? "true" : v);
  const r = await api("/tickets?" + p);
  $("#tt").innerHTML = tablaTickets(r.items) + `<div class="paginacion">${r.total} ticket(s) · página ${r.pagina} de ${r.paginas}
    <button class="secundario mini" id="ta" ${r.pagina <= 1 ? "disabled" : ""}>Anterior</button>
    <button class="secundario mini" id="ts" ${r.pagina >= r.paginas ? "disabled" : ""}>Siguiente</button></div>`;
  $("#ta").onclick = () => { filtrosT.pagina--; cargarConsola(); };
  $("#ts").onclick = () => { filtrosT.pagina++; cargarConsola(); };
  ligarTabla();
}

const CAMPOS_SELECT = {
  categoria_incidente: ["I0", "I1", "I2", "I3", "I4", "I5", "I6"], afectacion: ["NINGUNA", "DEGRADACION", "INTERRUPCION", "SEGURIDAD"],
  categoria_evento: ["E0", "E1", "E2", "E3"], accion_acceso: ["PROVISION", "REVOCACION"],
};
const AUTOMATICOS = { ci_afectados: "CI asociados al ticket", incidentes_vinculados: "incidentes relacionados", aprobado_por_id: "aprobación previa",
  responsable_id: "responsable asignado", servicio_resultante_id: "se calcula al implementar" };

async function fichaTicket(id) {
  const t = await api("/tickets/" + id);
  const r = await referencias();
  const p = t.permisos;
  const fila = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
  const cuerpo = dialogo(`<h2>${esc(t.numero)} · ${esc(t.titulo)}</h2>
    <dl class="ficha">
      ${fila("Proceso / tipo", `${esc(t.proceso_codigo)} / ${esc(t.tipo_ticket)}`)}
      ${fila("Estado", etqEstado(t.estado))}
      ${fila("Servicio", `${esc(t.servicio.codigo)} · ${esc(t.servicio.nombre)} <small>(N1 ${esc(t.servicio.n1_codigo)} · ${esc(t.servicio.n1_nombre)})</small>`)}
      ${fila("Cola / nivel", `${esc(t.cola.codigo)} · nivel ${t.nivel_soporte}`)}
      ${fila("Prioridad / impacto / urgencia", `${PRIORIDAD[t.prioridad]} / ${esc(t.impacto)} / ${esc(t.urgencia)}`)}
      ${fila("Canal", esc(t.canal))}
      ${t.categoria_incidente || t.afectacion ? fila("Incidente", `${esc(t.categoria_incidente) || "—"} · ${esc(t.afectacion) || "—"} · estimada ${t.duracion_estimada_min ?? "—"} min · real ${t.duracion_real_min ?? "—"} min · redundancia ${t.redundancia_disponible ?? "—"}`) : ""}
      ${t.categoria_evento ? fila("Evento", esc(t.categoria_evento)) : ""}
      ${fila("CI", t.cis.map((c) => esc(c.codigo)).join(", ") || `<i>${esc(t.sin_ci_motivo)}</i>`)}
      ${fila("Relaciones", t.relaciones.map((x) => `${esc(x.origen_numero)} ${esc(x.tipo)} ${esc(x.destino_numero)}`).join("<br>") || "—")}
      ${fila("Fechas", `creado ${fecha(t.creado_en)} · 1.ª atención ${fecha(t.primera_atencion_en)} · resuelto ${fecha(t.resuelto_en)} · cerrado ${fecha(t.cerrado_en)} · reaperturas ${t.reaperturas}`)}
      ${fila("Datos del proceso", `<pre>${esc(JSON.stringify(t.datos, null, 1))}</pre>`)}
    </dl>
    <div class="acciones">
      ${!t.responsable_id && p.atender && t.estado !== "CERRADO" ? '<button id="a-tomar">Tomar</button>' : ""}
      ${t.transiciones.map((x) => `<button class="secundario" data-h="${x.hacia}">→ ${esc(x.hacia)}</button>`).join("")}
      ${p.transferir && t.estado !== "CERRADO" ? '<button class="secundario" id="a-transf">Transferir / escalar</button>' : ""}
      ${t.estado === "CERRADO" ? '<button class="secundario" id="a-reabrir">Reabrir</button>' : ""}
    </div>
    <h3>Comentarios</h3>${t.comentarios.map((c) => `<div><b>${esc(c.autor)}</b> ${c.interno ? '<span class="etq rev">interno</span>' : ""} ${fecha(c.fecha)}: ${esc(c.texto)}</div>`).join("") || "—"}
    <form id="f-com" class="acciones"><input name="texto" placeholder="Comentario" required style="max-width:360px">
      ${p.atender ? '<label><span><input type="checkbox" name="interno" style="width:auto"> Nota interna</span></label>' : ""}<button>Comentar</button></form>
    <h3>Adjuntos</h3>${t.adjuntos.map((a) => `<div><a href="/api/tickets/${t.id}/adjuntos/${a.id}">${esc(a.nombre)}</a> (${a.tamano} B)</div>`).join("") || "—"}
    <form id="f-adj" class="acciones"><input type="file" name="archivo" accept=".pdf,.png,.jpg,.jpeg,.txt" required style="max-width:300px"><button>Subir (máx. 2 MB)</button></form>
    ${t.bitacora.length ? `<details><summary>Bitácora (${t.bitacora.length})</summary><table><tr><th>Fecha</th><th>Actor</th><th>Acción</th><th>Anterior</th><th>Nuevo</th><th>Motivo</th></tr>
      ${t.bitacora.map((b) => `<tr><td>${fecha(b.fecha)}</td><td>${esc(b.actor)}</td><td>${esc(b.accion)}</td><td><pre>${esc(JSON.stringify(b.anterior))}</pre></td><td><pre>${esc(JSON.stringify(b.nuevo))}</pre></td><td>${esc(b.motivo)}</td></tr>`).join("")}</table></details>` : ""}
    <p class="error" id="t-err"></p>
    <div class="acciones"><button class="secundario" id="t-cerrar">Cerrar</button></div>`);
  const err = (e) => ($("#t-err", cuerpo).textContent = e.message);
  const recargar = () => fichaTicket(id);
  $("#t-cerrar", cuerpo).onclick = cerrar;
  $("#a-tomar", cuerpo)?.addEventListener("click", () => api(`/tickets/${id}/tomar`, { method: "POST" }).then(recargar).catch(err));
  $("#a-reabrir", cuerpo)?.addEventListener("click", () => {
    const motivo = prompt("Motivo de la reapertura");
    if (motivo) api(`/tickets/${id}/reabrir`, { method: "POST", body: { motivo } }).then(recargar).catch(err);
  });
  $("#a-transf", cuerpo)?.addEventListener("click", () => {
    const destinos = r.colas.filter((c) => c.proceso_codigo === t.proceso_codigo && c.id !== t.cola_id && c.activo);
    const c2 = dialogo(`<h2>Transferir o escalar ${esc(t.numero)}</h2><form id="f-tr">
      <label>Cola destino <select name="cola_id">${opciones(destinos, null, (c) => `${c.codigo} · nivel ${c.nivel_soporte}`, "Seleccione…")}</select></label>
      <label class="ancho">Motivo <input name="motivo" required></label>
      <div class="acciones ancho"><button value="transferir">Transferir</button><button value="escalar" class="secundario">Escalar</button></div>
      <p class="error ancho" id="tr-err"></p></form>`);
    $("#f-tr", c2).onsubmit = (e) => {
      e.preventDefault();
      api(`/tickets/${id}/${e.submitter.value}`, { method: "POST", body: formDatos(e.target) }).then(() => { aviso("Ticket movido"); recargar(); })
        .catch((x) => ($("#tr-err", c2).textContent = x.message));
    };
  });
  cuerpo.querySelectorAll("[data-h]").forEach((b) => (b.onclick = () => formTransicion(t, b.dataset.h, r)));
  $("#f-com", cuerpo).onsubmit = (e) => {
    e.preventDefault();
    const d = formDatos(e.target);
    api(`/tickets/${id}/comentarios`, { method: "POST", body: { texto: d.texto, interno: !!d.interno } }).then(recargar).catch(err);
  };
  $("#f-adj", cuerpo).onsubmit = async (e) => {
    e.preventDefault();
    const r2 = await fetch(`/api/tickets/${id}/adjuntos`, { method: "POST", body: new FormData(e.target), credentials: "same-origin" });
    if (r2.ok) recargar(); else err(new Error((await r2.json()).detail));
  };
}

function formTransicion(t, hacia, r) {
  const tr = t.transiciones.find((x) => x.hacia === hacia);
  const campos = [...tr.requiere];
  if (t.proceso_codigo === "INCIDENTE" && hacia === "CLASIFICADO") campos.push("duracion_estimada_min", "justificacion_categoria");
  if (t.proceso_codigo === "INCIDENTE" && hacia === "RECUPERADO") campos.push("duracion_real_min");
  const input = (c) => {
    if (AUTOMATICOS[c]) return `<p class="ancho"><small>Requiere: ${esc(AUTOMATICOS[c])}</small></p>`;
    if (CAMPOS_SELECT[c]) return `<label>${c} <select name="${c}">${CAMPOS_SELECT[c].map((v) => `<option>${v}</option>`).join("")}</select></label>`;
    if (c === "servicio_propuesto") return `<label class="ancho">Vincular servicio existente <select name="vincular_id">${opciones(r.servicios, null, (s) => `${s.codigo} · ${s.nombre}`, "— crear uno nuevo —")}</select></label>
      <label>Nuevo código <input name="np_codigo"></label><label>Nuevo nombre <input name="np_nombre"></label>
      <label>Nivel 1 <select name="np_n1">${opciones(r.n1, null, (n) => n.codigo)}</select></label>`;
    if (c === "beneficiario_id") return `<label>${c} <input name="${c}" type="number" value="${t.beneficiario_id || ""}"></label>`;
    return `<label class="${c.includes("plan") || c.includes("solucion") ? "ancho" : ""}">${c} <input name="${c}" ${c.endsWith("_min") ? 'type="number"' : ""}></label>`;
  };
  const cuerpo = dialogo(`<h2>${esc(t.numero)}: ${esc(t.estado)} → ${esc(hacia)}</h2><form id="f-trans">
    ${campos.map(input).join("")}<label class="ancho">Motivo / comentario <input name="_motivo"></label>
    <p class="error ancho" id="x-err"></p><div class="acciones ancho"><button>Confirmar</button><button type="button" class="secundario" id="x-c">Cancelar</button></div></form>`);
  $("#x-c", cuerpo).onclick = () => fichaTicket(t.id);
  $("#f-trans", cuerpo).onsubmit = (e) => {
    e.preventDefault();
    const d = formDatos(e.target);
    const motivo = d._motivo; delete d._motivo;
    if ("vincular_id" in d || "np_codigo" in d) {
      d.servicio_propuesto = d.vincular_id ? { vincular_id: +d.vincular_id } : { codigo: d.np_codigo, nombre: d.np_nombre, servicio_n1_id: +d.np_n1 };
      ["vincular_id", "np_codigo", "np_nombre", "np_n1"].forEach((k) => delete d[k]);
    }
    for (const k of ["duracion_estimada_min", "duracion_real_min", "beneficiario_id"]) if (d[k] != null) d[k] = +d[k];
    for (const k of Object.keys(d)) if (d[k] == null) delete d[k];
    api(`/tickets/${t.id}/transicion`, { method: "POST", body: { hacia, datos: d, motivo } })
      .then(() => { aviso(`Ticket en ${hacia}`); fichaTicket(t.id); }).catch((x) => ($("#x-err", cuerpo).textContent = x.message));
  };
}

// ------------------------------------------------------------------ colas
async function vistaColas() {
  refSD = null;
  const r = await referencias();
  const niveles = await api("/niveles-soporte");
  $("#contenido").innerHTML = `<div class="panel"><h2>Colas de atención</h2>
    ${esAdmin() ? '<div class="acciones"><button id="c-nueva">Nueva cola</button></div>' : ""}
    <table><thead><tr><th>Código</th><th>Nombre</th><th>Proceso</th><th>Nivel</th><th>Orden</th><th>Objetivos (min)</th><th>Abiertos</th><th>Estado</th><th></th></tr></thead><tbody>
    ${r.colas.map((c) => `<tr><td>${esc(c.codigo)}</td><td>${esc(c.nombre)}</td><td>${esc(c.proceso_nombre)}</td><td>${c.nivel_soporte} · ${esc(c.nivel_nombre)}</td>
      <td>${esc(c.regla_orden)}</td><td>${c.objetivo_primera_atencion_min ?? "—"} / ${c.objetivo_resolucion_min ?? "—"}</td><td>${c.abiertos}</td>
      <td>${c.activo ? '<span class="etq ok">activa</span>' : '<span class="etq no">inactiva</span>'}</td>
      <td><button class="mini secundario" data-m="${c.id}">Miembros</button> <button class="mini" data-sig="${c.id}">Tomar siguiente</button></td></tr>`).join("")}</tbody></table></div>
    <div class="panel"><h3>Niveles de soporte (configurables)</h3><table><tr><th>Nivel</th><th>Nombre</th><th>Equivalencia con las diapositivas</th><th>Referencia</th></tr>
      ${niveles.map((n) => `<tr><td>${n.nivel}</td><td>${esc(n.nombre)}</td><td>${esc(n.equivalencia)}</td><td>${esc(n.referencia_porcentaje)}</td></tr>`).join("")}</table></div><div id="miembros"></div>`;
  document.querySelectorAll("[data-sig]").forEach((b) => (b.onclick = () => api(`/colas/${b.dataset.sig}/tomar-siguiente`, { method: "POST" })
    .then((t) => { aviso(`Tomó ${t.numero}`); fichaTicket(t.id); }).catch((e) => aviso(e.message))));
  document.querySelectorAll("[data-m]").forEach((b) => (b.onclick = async () => {
    const m = await api(`/colas/${b.dataset.m}/miembros`);
    const f = (v) => (v ? "✔" : "—");
    $("#miembros").innerHTML = `<div class="panel"><h3>Miembros de la cola</h3><table><tr><th>Usuario / rol</th><th>Consultar</th><th>Atender</th><th>Transferir</th><th>Aprobar</th><th>Administrar</th><th>Activo</th></tr>
      ${m.map((x) => `<tr><td>${x.usuario ? esc(x.usuario) : "rol: " + esc(x.rol)}</td><td>${f(x.puede_consultar)}</td><td>${f(x.puede_atender)}</td><td>${f(x.puede_transferir)}</td><td>${f(x.puede_aprobar)}</td><td>${f(x.puede_administrar)}</td><td>${f(x.activo)}</td></tr>`).join("")}</table>
      ${esAdmin() ? `<form id="f-mb" class="filtros"><label>Usuario (id) <input name="usuario_id" type="number"></label><label>o rol <select name="rol"><option value="">—</option>${["agente", "aprobador", "consulta", "solicitante", "administrador"].map((x) => `<option>${x}</option>`).join("")}</select></label>
        ${["puede_consultar", "puede_atender", "puede_transferir", "puede_aprobar", "puede_administrar"].map((k) => `<label><span><input type="checkbox" name="${k}" style="width:auto" ${k === "puede_consultar" ? "checked" : ""}> ${k.replace("puede_", "")}</span></label>`).join("")}
        <button>Agregar</button></form>` : ""}</div>`;
    $("#f-mb")?.addEventListener("submit", (e) => {
      e.preventDefault();
      const d = formDatos(e.target);
      for (const k of Object.keys(d)) if (k.startsWith("puede_")) d[k] = true;
      if (!d.usuario_id) delete d.usuario_id; else d.usuario_id = +d.usuario_id;
      if (!d.rol) delete d.rol;
      api(`/colas/${b.dataset.m}/miembros`, { method: "POST", body: d }).then(() => b.click()).catch((x) => aviso(x.message));
    });
  }));
  $("#c-nueva")?.addEventListener("click", () => {
    const c = dialogo(`<h2>Nueva cola</h2><form id="f-cn">
      <label>Código <input name="codigo" required></label><label>Nombre <input name="nombre" required></label>
      <label>Proceso <select name="proceso_codigo">${r.procesos.map((p) => `<option value="${p.codigo}">${esc(p.nombre)}</option>`).join("")}</select></label>
      <label>Nivel <select name="nivel_soporte">${niveles.map((n) => `<option value="${n.nivel}">${n.nivel} · ${esc(n.nombre)}</option>`).join("")}</select></label>
      <label>Objetivo 1.ª atención (min) <input name="objetivo_primera_atencion_min" type="number"></label>
      <label>Objetivo resolución (min) <input name="objetivo_resolucion_min" type="number"></label>
      <label class="ancho">Descripción <input name="descripcion"></label><p class="error ancho" id="cn-err"></p>
      <div class="acciones ancho"><button>Guardar</button></div></form>`);
    $("#f-cn", c).onsubmit = (e) => { e.preventDefault(); api("/colas", { method: "POST", body: formDatos(e.target) }).then(() => { cerrar(); vistaColas(); }).catch((x) => ($("#cn-err", c).textContent = x.message)); };
  });
}

// ------------------------------------------------------------------ CMDB
const filtrosCI = { pagina: 1 };
async function vistaCMDB() {
  const [clases, ubic, res] = await Promise.all([api("/cmdb/clases"), api("/cmdb/ubicaciones"), api("/cmdb/resumen")]);
  const arbol = (padre, nivel) => clases.filter((c) => c.padre_id === padre).map((c) =>
    `<div style="margin-left:${nivel * 18}px">${nivel ? "└ " : ""}<b>${esc(c.codigo)}</b> ${esc(c.nombre)} <small>(${esc(c.denominacion)} · ${c.cis} CI · ${esc(c.origen)})</small></div>${arbol(c.id, nivel + 1)}`).join("");
  $("#contenido").innerHTML = `<div class="panel"><h2>CMDB</h2><p>${res.total_dispositivos} dispositivos · ${Object.entries(res.relaciones).map(([k, v]) => `${k}: ${v}`).join(" · ")}</p>
      <details><summary>Grafo de clasificación (jerarquía)</summary>${arbol(null, 0)}</details></div>
    <div class="panel"><form id="fci" class="filtros">
      <label>Buscar código o nombre <input name="q" value="${esc(filtrosCI.q)}"></label>
      <label>Clase (incluye subclases) <select name="clase_id">${opciones(clases, filtrosCI.clase_id, (c) => `${"· ".repeat(c.nivel - 1)}${c.codigo} ${c.nombre}`, "Todas")}</select></label>
      <label>Estado <select name="estado"><option value="">Todos</option>${["Activo", "Inactivo", "En mantenimiento", "Retirado"].map((e) => `<option ${filtrosCI.estado === e ? "selected" : ""}>${e}</option>`).join("")}</select></label>
      <label>Ubicación <select name="ubicacion_id">${opciones(ubic, filtrosCI.ubicacion_id, (u) => u.nombre, "Todas")}</select></label>
      <button>Buscar</button></form></div><div class="panel" id="tci">Cargando…</div>`;
  $("#fci").onsubmit = (e) => { e.preventDefault(); Object.assign(filtrosCI, formDatos(e.target), { pagina: 1 }); cargarCIs(); };
  cargarCIs();
}
async function cargarCIs() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filtrosCI)) if (v) p.set(k, v);
  const r = await api("/cmdb/cis?" + p);
  $("#tci").innerHTML = `<table><thead><tr><th>Código</th><th>Nombre</th><th>Clase</th><th>Estado</th><th>Ubicación</th><th>Responsable</th><th>Lote</th></tr></thead><tbody>
    ${r.items.map((c) => `<tr class="clic" data-ci="${c.id}"><td>${esc(c.codigo)}</td><td>${esc(c.nombre)}</td><td>${esc(c.clase_nombre)}</td><td>${esc(c.estado_actual)}</td><td>${esc(c.ubicacion)}</td><td>${esc(c.responsable)}</td><td>${esc(c.lote)}</td></tr>`).join("") || '<tr><td colspan="7">Sin resultados</td></tr>'}</tbody></table>
    <div class="paginacion">${r.total} CI · página ${r.pagina} de ${r.paginas} · ${r.tiempo_ms} ms (servidor)
      <button class="secundario mini" id="ca" ${r.pagina <= 1 ? "disabled" : ""}>Anterior</button><button class="secundario mini" id="cs" ${r.pagina >= r.paginas ? "disabled" : ""}>Siguiente</button></div>`;
  $("#ca").onclick = () => { filtrosCI.pagina--; cargarCIs(); };
  $("#cs").onclick = () => { filtrosCI.pagina++; cargarCIs(); };
  document.querySelectorAll("[data-ci]").forEach((tr) => (tr.onclick = () => fichaCI(tr.dataset.ci)));
}
async function fichaCI(id) {
  const c = await api("/cmdb/cis/" + id);
  const enlace = (x) => `<a href="#" data-ir="${x.ci_id}">${esc(x.codigo)}</a> ${esc(x.nombre)}`;
  const cuerpo = dialogo(`<h2>${esc(c.codigo)} · ${esc(c.nombre)}</h2>
    <p><b>Clasificación:</b> ${c.clasificacion.map((x) => `${esc(x.denominacion)}: ${esc(x.nombre)}`).join(" → ")}</p>
    <dl class="ficha">${["descripcion", "numero_serie", "version", "fecha_adquisicion", "estado_actual", "ubicacion", "responsable", "responsable_original",
      "nivel_seguridad", "cumplimiento", "estado_configuracion", "numero_licencia", "fecha_vencimiento", "lote"].map((k) => `<dt>${k}</dt><dd>${esc(c[k]) || "—"}</dd>`).join("")}
      <dt>Atributos técnicos</dt><dd><pre>${esc(JSON.stringify(c.atributos, null, 1))}</pre></dd></dl>
    <h3>Relaciones técnicas (navegables)</h3>
    ${c.relaciones_salientes.map((x) => `<div>este CI <b>${esc(x.tipo_nombre)}</b> ${enlace(x)}</div>`).join("")}
    ${c.relaciones_entrantes.map((x) => `<div>${enlace(x)} <b>${esc(x.tipo_nombre)}</b> este CI</div>`).join("") || (c.relaciones_salientes.length ? "" : "—")}
    <h3>Servicios asociados</h3>${c.servicios.map((s) => `${esc(s.codigo)} · ${esc(s.nombre)}`).join("<br>") || "—"}
    <h3>Historial de tickets</h3>${c.tickets.map((t) => `${esc(t.numero)} · ${esc(t.proceso_codigo)} · ${esc(t.estado)} · ${esc(t.titulo)}`).join("<br>") || "—"}
    <div class="acciones"><button id="ci-imp">Recorrer impacto</button><button class="secundario" id="ci-c">Cerrar</button></div><div id="imp"></div>`);
  $("#ci-c", cuerpo).onclick = cerrar;
  cuerpo.querySelectorAll("[data-ir]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); fichaCI(a.dataset.ir); }));
  $("#ci-imp", cuerpo).onclick = async () => {
    const r = await api(`/cmdb/cis/${id}/impacto`);
    $("#imp", cuerpo).innerHTML = `<p><i>${esc(r.nota)}</i></p><b>CI potencialmente afectados (${r.cis_afectados.length}):</b> ${r.cis_afectados.map((x) => `${esc(x.codigo)} (prof. ${x.prof})`).join(", ")}
      <br><b>Servicios potencialmente afectados:</b> ${r.servicios_potencialmente_afectados.map((s) => esc(s.codigo)).join(", ") || "ninguno"}`;
  };
}

// ------------------------------------------------------------------ tablero
async function vistaTablero() {
  const k = await api("/indicadores");
  const tabla = (titulo, obj, etq = (x) => x) => `<div class="panel"><h3>${titulo}</h3><table>${Object.entries(obj).map(([a, b]) => `<tr><td>${esc(etq(a))}</td><td>${b}</td></tr>`).join("")}</table></div>`;
  const st = (s) => (s.n ? `${s.promedio} min promedio · máx. ${s.maximo} · n=${s.n}` : "sin datos");
  const cu = (c) => (c.medibles ? `${c.porcentaje} % (${c.cumplen}/${c.medibles})` : "sin datos");
  $("#contenido").innerHTML = `<div class="panel"><h2>Tablero de atención</h2>
      <p>Total ${k.total} · abiertos ${k.abiertos} · reaperturas ${k.reaperturas} · base de tiempo: <b>${k.base_tiempo}</b></p>
      <dl class="ficha"><dt>Antigüedad de abiertos</dt><dd>${st(k.antiguedad_abiertos_min)}</dd>
        <dt>Tiempo de primera atención</dt><dd>${st(k.primera_atencion_min)}</dd>
        <dt>Tiempo de resolución (descuenta pausas)</dt><dd>${st(k.resolucion_min)} · ${k.tickets_sin_resolver_excluidos} sin resolver excluidos</dd>
        <dt>Cumplimiento 1.ª atención</dt><dd>${cu(k.cumplimiento_primera_atencion)}</dd>
        <dt>Cumplimiento resolución</dt><dd>${cu(k.cumplimiento_resolucion)}</dd></dl></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem">
      ${tabla("Por cola", k.por_cola)}${tabla("Por proceso", k.por_proceso)}${tabla("Por prioridad", k.por_prioridad, (p) => PRIORIDAD[p])}${tabla("Por estado", k.por_estado)}</div>`;
}

window.VISTAS_SD = { portal: vistaPortal, consola: vistaConsola, colas: vistaColas, cmdb: vistaCMDB, tablero: vistaTablero };
