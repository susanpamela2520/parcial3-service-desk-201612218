// Interfaz del catálogo. La autorización real está en el servidor; aquí solo se ocultan
// controles para comodidad. Todo texto proveniente de datos se escapa con esc().
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const vacio = (v) => (v === null || v === undefined || v === "" ? '<span class="etq gris">desconocido</span>' : esc(v));
let yo = null;
const esAdmin = () => yo && yo.rol === "administrador";

async function api(ruta, opciones = {}) {
  const r = await fetch("/api" + ruta, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...opciones,
    body: opciones.body ? JSON.stringify(opciones.body) : undefined,
  });
  if (r.status === 401 && ruta !== "/auth/login") { mostrarLogin(); throw new Error("Sesión no válida"); }
  const datos = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(datos.detail || `Error ${r.status}`);
    e.datos = datos;
    throw e;
  }
  return datos;
}

function aviso(msg) {
  const a = $("#aviso");
  a.textContent = msg;
  a.classList.add("ver");
  clearTimeout(a._t);
  a._t = setTimeout(() => a.classList.remove("ver"), 3500);
}

function dialogo(html) {
  $("#dialogo-cuerpo").innerHTML = html;
  $("#dialogo").showModal();
  return $("#dialogo-cuerpo");
}
const cerrar = () => $("#dialogo").close();

const formDatos = (form) => {
  const d = {};
  for (const [k, v] of new FormData(form)) d[k] = v === "" ? null : v;
  return d;
};

const opciones = (lista, sel, etiqueta = (o) => o.nombre, vacia = "— sin valor —") =>
  `<option value="">${vacia}</option>` +
  lista.map((o) => `<option value="${o.id}" ${String(o.id) === String(sel ?? "") ? "selected" : ""}>${esc(etiqueta(o))}</option>`).join("");

// ------------------------------------------------------------------ sesión
function mostrarLogin() {
  yo = null;
  $("#vista-app").hidden = true;
  $("#vista-login").hidden = false;
}

async function iniciar() {
  try {
    yo = await api("/auth/me");
  } catch { return mostrarLogin(); }
  $("#vista-login").hidden = true;
  $("#vista-app").hidden = false;
  $("#quien").textContent = `${yo.nombre} · ${yo.rol}`;
  ir(yo.rol === "solicitante" ? "portal" : yo.rol === "agente" || yo.rol === "aprobador" ? "consola" : "servicios");
}

$("#form-login").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    await api("/auth/login", { method: "POST", body: formDatos(e.target) });
    e.target.reset();
    iniciar();
  } catch (err) { $("#login-error").textContent = err.message; }
});

$("#btn-salir").addEventListener("click", async () => {
  await api("/auth/logout", { method: "POST" }).catch(() => {});
  mostrarLogin();
});

$("#menu").addEventListener("click", (e) => { if (e.target.dataset.vista) ir(e.target.dataset.vista); });

function ir(vista) {
  document.querySelectorAll("#menu button").forEach((b) => b.classList.toggle("activo", b.dataset.vista === vista));
  const vistas = { servicios: vistaServicios, n1: vistaN1, org: vistaOrg, usuarios: vistaUsuarios, catalogos: vistaCatalogos,
    importacion: vistaImportacion, ...(window.VISTAS_SD || {}) };
  vistas[vista]().catch?.((e) => aviso(e.message));
}

// ------------------------------------------------------------------ servicios N2
let cache = {};
async function catalogos() {
  if (!cache.clases) {
    const [clases, criticidades, tipos, n1] = await Promise.all([
      api("/catalogos/clases"), api("/catalogos/criticidades"), api("/catalogos/tipos"), api("/servicios-n1")]);
    cache = { clases, criticidades, tipos, n1 };
  }
  return cache;
}

const filtrosServicios = { pagina: 1 };

async function vistaServicios() {
  const c = await catalogos();
  $("#contenido").innerHTML = `
    <div class="panel">
      <form id="filtros" class="filtros">
        <label>Buscar código o nombre <input name="q" value="${esc(filtrosServicios.q)}"></label>
        <label>Nivel 1 <select name="n1_id">${opciones(c.n1, filtrosServicios.n1_id, (o) => `${o.codigo} · ${o.nombre}`, "Todos")}</select></label>
        <label>Estado (ACTIVO) <select name="activo">
          ${["", "S", "N", "DESCONOCIDO"].map((v) => `<option value="${v}" ${filtrosServicios.activo === v ? "selected" : ""}>${v || "Todos"}</option>`).join("")}
        </select></label>
        <label>Clase <select name="clase_id">${opciones(c.clases, filtrosServicios.clase_id, undefined, "Todas")}</select></label>
        <label>Criticidad <select name="criticidad_id">${opciones(c.criticidades, filtrosServicios.criticidad_id, undefined, "Todas")}</select></label>
        <label>Tipo <select name="tipo_id">${opciones(c.tipos, filtrosServicios.tipo_id, undefined, "Todos")}</select></label>
        <label>Revisión <select name="revision">
          ${["", "OK", "REVISION"].map((v) => `<option value="${v}" ${filtrosServicios.revision === v ? "selected" : ""}>${v || "Todas"}</option>`).join("")}
        </select></label>
        <label><span><input type="checkbox" name="incluir_baja" style="width:auto" ${filtrosServicios.incluir_baja ? "checked" : ""}> Incluir dados de baja</span></label>
        <button type="submit">Filtrar</button>
      </form>
    </div>
    <div class="panel">
      <div class="acciones"><button id="nuevo-srv" class="solo-admin" ${esAdmin() ? "" : "hidden"}>Nuevo servicio</button></div>
      <div id="tabla-srv">Cargando…</div>
    </div>`;
  $("#filtros").addEventListener("submit", (e) => {
    e.preventDefault();
    const d = formDatos(e.target);
    Object.assign(filtrosServicios, d, { incluir_baja: !!d.incluir_baja, pagina: 1 });
    cargarServicios();
  });
  $("#nuevo-srv").addEventListener("click", () => formServicio());
  cargarServicios();
}

async function cargarServicios() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filtrosServicios)) if (v) p.set(k, v === true ? "true" : v);
  const r = await api("/servicios?" + p);
  $("#tabla-srv").innerHTML = `
    <table><thead><tr><th>Código</th><th>Servicio</th><th>Nivel 1</th><th>Activo</th><th>Clase</th><th>Criticidad</th><th>Tipo</th><th>Sección</th><th>Revisión</th></tr></thead>
    <tbody>${r.items.map((s) => `
      <tr class="clic" data-id="${s.id}">
        <td>${esc(s.codigo)}</td><td>${esc(s.nombre)}${s.dado_de_baja ? ' <span class="etq no">baja</span>' : ""}</td>
        <td>${esc(s.n1.codigo)}</td>
        <td>${s.activo_texto === "DESCONOCIDO" ? '<span class="etq gris">?</span>' : esc(s.activo)}</td>
        <td>${vacio(s.clase?.nombre)}</td><td>${vacio(s.criticidad?.nombre)}</td><td>${vacio(s.tipo?.nombre)}</td>
        <td>${s.seccion ? esc(s.seccion.codigo) : "—"}</td>
        <td><span class="etq ${s.estado_revision === "OK" ? "ok" : "rev"}">${esc(s.estado_revision)}</span></td>
      </tr>`).join("") || '<tr><td colspan="9">Sin resultados</td></tr>'}</tbody></table>
    <div class="paginacion">
      ${r.total} servicio(s) · página ${r.pagina} de ${r.paginas}
      <button class="secundario mini" id="ant" ${r.pagina <= 1 ? "disabled" : ""}>Anterior</button>
      <button class="secundario mini" id="sig" ${r.pagina >= r.paginas ? "disabled" : ""}>Siguiente</button>
    </div>`;
  $("#ant").onclick = () => { filtrosServicios.pagina--; cargarServicios(); };
  $("#sig").onclick = () => { filtrosServicios.pagina++; cargarServicios(); };
  document.querySelectorAll("#tabla-srv tr.clic").forEach((tr) => tr.addEventListener("click", () => fichaServicio(tr.dataset.id)));
}

async function fichaServicio(id) {
  const s = await api("/servicios/" + id);
  const fila = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
  const orig = (v, o) => vacio(v) + (o && o !== v ? ` <small>(original: ${esc(o)})</small>` : "");
  const cuerpo = dialogo(`
    <h2>${esc(s.codigo)} · ${esc(s.nombre)}</h2>
    <dl class="ficha">
      ${fila("Nivel 1", `${esc(s.n1.codigo)} · ${esc(s.n1.nombre)}`)}
      ${fila("Código original", esc(s.codigo_original))}
      ${fila("Activo (Excel)", orig(s.activo, s.activo_original))}
      ${fila("Clase", orig(s.clase?.nombre, s.clase_original))}
      ${fila("Criticidad", orig(s.criticidad?.nombre, s.criticidad_original))}
      ${fila("Tipo", orig(s.tipo?.nombre, s.tipo_original))}
      ${fila("Descripción", vacio(s.descripcion))}
      ${fila("Métrica", vacio(s.metrica))}
      ${fila("Mínimo", orig(s.minimo, s.minimo_original))}
      ${fila("Máximo", orig(s.maximo, s.maximo_original))}
      ${fila("Estado de revisión", `<span class="etq ${s.estado_revision === "OK" ? "ok" : "rev"}">${esc(s.estado_revision)}</span>`)}
      ${fila("Baja lógica", s.dado_de_baja ? "Sí" : "No")}
      ${fila("Sección responsable", s.seccion ? `${esc(s.seccion.codigo)} · ${esc(s.seccion.nombre)}` : "Sin asignar")}
      ${fila("Usuario responsable", s.responsable ? `${esc(s.responsable.nombre)} (${esc(s.responsable.usuario)})` : "Sin asignar")}
      ${fila("Origen", `${esc(s.origen_hoja)} ${esc(s.origen_rango)}`)}
    </dl>
    <details><summary>Valores de origen</summary><pre>${esc(JSON.stringify(s.origen_valores, null, 2))}</pre></details>
    <div class="acciones">
      ${esAdmin() ? `<button id="f-editar">Editar</button><button id="f-asignar">Asignar responsable</button>
        <button id="f-baja" class="${s.dado_de_baja ? "" : "peligro"}">${s.dado_de_baja ? "Reactivar" : "Dar de baja"}</button>` : ""}
      <button class="secundario" id="f-cerrar">Cerrar</button>
    </div>`);
  $("#f-cerrar", cuerpo).onclick = cerrar;
  if (esAdmin()) {
    $("#f-editar", cuerpo).onclick = () => formServicio(s);
    $("#f-asignar", cuerpo).onclick = () => formAsignacion(s);
    $("#f-baja", cuerpo).onclick = async () => {
      try { await api(`/servicios/${s.id}/${s.dado_de_baja ? "activar" : "desactivar"}`, { method: "POST" }); cerrar(); cargarServicios(); aviso("Servicio actualizado"); }
      catch (e) { aviso(e.message); }
    };
  }
}

async function formServicio(s = null) {
  const c = await catalogos();
  const cuerpo = dialogo(`
    <h2>${s ? "Editar " + esc(s.codigo) : "Nuevo servicio de nivel 2"}</h2>
    <form id="f-srv">
      <label>Nivel 1 <select name="servicio_n1_id" required>${opciones(c.n1.filter((o) => o.activo || o.id === s?.n1.id), s?.n1.id, (o) => `${o.codigo} · ${o.nombre}`, "Seleccione…")}</select></label>
      <label>Código <input name="codigo" required value="${esc(s?.codigo)}"></label>
      <label class="ancho">Nombre <input name="nombre" required value="${esc(s?.nombre)}"></label>
      <label>Activo <select name="activo">${["", "S", "N"].map((v) => `<option value="${v}" ${(s?.activo ?? "") === v ? "selected" : ""}>${v || "Desconocido"}</option>`).join("")}</select></label>
      <label>Clase <select name="clase_id">${opciones(c.clases.filter((o) => o.activo), s?.clase?.id)}</select></label>
      <label>Criticidad <select name="criticidad_id">${opciones(c.criticidades.filter((o) => o.activo), s?.criticidad?.id)}</select></label>
      <label>Tipo <select name="tipo_id">${opciones(c.tipos.filter((o) => o.activo), s?.tipo?.id)}</select></label>
      <label class="ancho">Descripción <textarea name="descripcion" rows="2">${esc(s?.descripcion)}</textarea></label>
      <label class="ancho">Métrica <input name="metrica" value="${esc(s?.metrica)}"></label>
      <label>Mínimo <input name="minimo" type="number" step="any" value="${esc(s?.minimo)}"></label>
      <label>Máximo <input name="maximo" type="number" step="any" value="${esc(s?.maximo)}"></label>
      <p class="error ancho" id="f-err"></p>
      <div class="acciones ancho"><button type="submit">Guardar</button><button type="button" class="secundario" id="f-cancel">Cancelar</button></div>
    </form>`);
  $("#f-cancel", cuerpo).onclick = cerrar;
  $("#f-srv", cuerpo).addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(s ? `/servicios/${s.id}` : "/servicios", { method: s ? "PUT" : "POST", body: formDatos(e.target) });
      cerrar(); aviso("Servicio guardado"); cargarServicios();
    } catch (err) { $("#f-err", cuerpo).textContent = err.message; }
  });
}

async function formAsignacion(s) {
  const secciones = await api("/org/secciones?activo=true");
  const cuerpo = dialogo(`
    <h2>Asignar responsable · ${esc(s.codigo)}</h2>
    <form id="f-asig">
      <label>Sección responsable <select name="seccion_id">${opciones(secciones, s.seccion?.id, (o) => `${o.codigo} · ${o.nombre}`, "Sin asignar")}</select></label>
      <label>Usuario responsable (opcional) <select name="responsable_id"></select></label>
      <p class="error ancho" id="a-err"></p>
      <div class="acciones ancho"><button type="submit">Guardar</button><button type="button" class="secundario" id="a-cancel">Cancelar</button></div>
    </form>`);
  const selSec = $("[name=seccion_id]", cuerpo), selUsr = $("[name=responsable_id]", cuerpo);
  const cargarUsuarios = async () => {
    const lista = selSec.value ? await api(`/usuarios?seccion_id=${selSec.value}&activo=true`) : [];
    selUsr.innerHTML = opciones(lista, s.responsable?.id, (u) => `${u.nombre} (${u.usuario})`, "Sin responsable");
  };
  selSec.onchange = cargarUsuarios;
  cargarUsuarios();
  $("#a-cancel", cuerpo).onclick = cerrar;
  $("#f-asig", cuerpo).addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api(`/servicios/${s.id}/asignacion`, { method: "PUT", body: formDatos(e.target) }); cerrar(); aviso("Asignación guardada"); cargarServicios(); }
    catch (err) { $("#a-err", cuerpo).textContent = err.message; }
  });
}

// ------------------------------------------------------------------ nivel 1
async function vistaN1() {
  cache = {};
  const lista = await api("/servicios-n1");
  $("#contenido").innerHTML = `<div class="panel">
    <div class="acciones">${esAdmin() ? '<button id="n1-nuevo">Nuevo nivel 1</button>' : ""}</div>
    <table><thead><tr><th>Código</th><th>Nombre</th><th>Nombres en el Excel</th><th>Servicios N2</th><th>Estado</th><th></th></tr></thead><tbody>
    ${lista.map((o) => `<tr><td>${esc(o.codigo)}</td><td>${esc(o.nombre)}</td>
      <td>${(o.nombres_alternativos || []).map((n) => `${esc(n.valor)} <small>(${esc(n.celda)})</small>`).join("<br>")}</td>
      <td>${o.total_n2}</td><td>${o.activo ? '<span class="etq ok">activo</span>' : '<span class="etq no">inactivo</span>'}</td>
      <td>${esAdmin() ? `<button class="mini secundario" data-ed="${o.id}">Editar</button>` : ""}</td></tr>`).join("")}
    </tbody></table></div>`;
  const editar = (o = {}) => {
    const cuerpo = dialogo(`<h2>${o.id ? "Editar" : "Nuevo"} servicio de nivel 1</h2><form id="f-n1">
      <label>Código <input name="codigo" required value="${esc(o.codigo)}"></label>
      <label>Nombre <input name="nombre" required value="${esc(o.nombre)}"></label>
      <label>Estado <select name="activo"><option value="true">Activo</option><option value="false" ${o.activo === false ? "selected" : ""}>Inactivo</option></select></label>
      <p class="error ancho" id="n1-err"></p>
      <div class="acciones ancho"><button>Guardar</button><button type="button" class="secundario" id="n1-c">Cancelar</button></div></form>`);
    $("#n1-c", cuerpo).onclick = cerrar;
    $("#f-n1", cuerpo).onsubmit = async (e) => {
      e.preventDefault();
      const d = formDatos(e.target); d.activo = d.activo === "true";
      try { await api(o.id ? `/servicios-n1/${o.id}` : "/servicios-n1", { method: o.id ? "PUT" : "POST", body: d }); cerrar(); vistaN1(); }
      catch (err) { $("#n1-err", cuerpo).textContent = err.message; }
    };
  };
  $("#n1-nuevo")?.addEventListener("click", () => editar());
  document.querySelectorAll("[data-ed]").forEach((b) => (b.onclick = () => editar(lista.find((o) => o.id == b.dataset.ed))));
}

// ------------------------------------------------------------------ organización
const NIVELES = [
  { ent: "empresas", titulo: "Empresas", padre: null },
  { ent: "areas", titulo: "Áreas", padre: { campo: "empresa_id", ent: "empresas", titulo: "Empresa" } },
  { ent: "departamentos", titulo: "Departamentos", padre: { campo: "area_id", ent: "areas", titulo: "Área" } },
  { ent: "secciones", titulo: "Secciones", padre: { campo: "departamento_id", ent: "departamentos", titulo: "Departamento" } },
  { ent: "puestos", titulo: "Puestos", padre: { campo: "seccion_id", ent: "secciones", titulo: "Sección" } },
];
let nivelOrg = 0;

async function vistaOrg() {
  const n = NIVELES[nivelOrg];
  const [lista, padres] = await Promise.all([api(`/org/${n.ent}`), n.padre ? api(`/org/${n.padre.ent}`) : []]);
  const nombrePadre = (id) => { const p = padres.find((x) => x.id === id); return p ? `${p.codigo} · ${p.nombre}` : "—"; };
  $("#contenido").innerHTML = `
    <div class="pestanas">${NIVELES.map((x, i) => `<button data-n="${i}" class="${i === nivelOrg ? "activo" : ""}">${x.titulo}</button>`).join("")}</div>
    <div class="panel">
      <div class="acciones">${esAdmin() ? `<button id="org-nuevo">Nuevo</button>` : ""}<small>Jerarquía: Empresa → Área → Departamento → Sección → Puesto → Usuario</small></div>
      <table><thead><tr><th>Código</th><th>Nombre</th>${n.padre ? `<th>${n.padre.titulo}</th>` : ""}<th>Estado</th><th></th></tr></thead><tbody>
      ${lista.map((o) => `<tr><td>${esc(o.codigo)}</td><td>${esc(o.nombre)}</td>${n.padre ? `<td>${esc(nombrePadre(o[n.padre.campo]))}</td>` : ""}
        <td>${o.activo ? '<span class="etq ok">activo</span>' : '<span class="etq no">inactivo</span>'}</td>
        <td>${esAdmin() ? `<button class="mini secundario" data-ed="${o.id}">Editar</button>
          <button class="mini ${o.activo ? "peligro" : ""}" data-est="${o.id}">${o.activo ? "Desactivar" : "Activar"}</button>` : ""}</td></tr>`).join("") || '<tr><td colspan="5">Sin registros</td></tr>'}
      </tbody></table></div>`;
  document.querySelectorAll(".pestanas button").forEach((b) => (b.onclick = () => { nivelOrg = +b.dataset.n; vistaOrg(); }));
  const editar = (o = {}) => {
    const cuerpo = dialogo(`<h2>${o.id ? "Editar" : "Nuevo"} · ${n.titulo}</h2><form id="f-org">
      ${n.padre ? `<label class="ancho">${n.padre.titulo} <select name="${n.padre.campo}" required>${opciones(padres.filter((p) => p.activo || p.id === o[n.padre.campo]), o[n.padre.campo], (p) => `${p.codigo} · ${p.nombre}`, "Seleccione…")}</select></label>` : ""}
      <label>Código <input name="codigo" required maxlength="30" value="${esc(o.codigo)}"></label>
      <label>Nombre <input name="nombre" required value="${esc(o.nombre)}"></label>
      <p class="error ancho" id="o-err"></p>
      <div class="acciones ancho"><button>Guardar</button><button type="button" class="secundario" id="o-c">Cancelar</button></div></form>`);
    $("#o-c", cuerpo).onclick = cerrar;
    $("#f-org", cuerpo).onsubmit = async (e) => {
      e.preventDefault();
      try { await api(o.id ? `/org/${n.ent}/${o.id}` : `/org/${n.ent}`, { method: o.id ? "PUT" : "POST", body: formDatos(e.target) }); cerrar(); vistaOrg(); aviso("Guardado"); }
      catch (err) { $("#o-err", cuerpo).textContent = err.message; }
    };
  };
  $("#org-nuevo")?.addEventListener("click", () => editar());
  document.querySelectorAll("[data-ed]").forEach((b) => (b.onclick = () => editar(lista.find((o) => o.id == b.dataset.ed))));
  document.querySelectorAll("[data-est]").forEach((b) => (b.onclick = () => cambiarEstado(`/org/${n.ent}`, lista.find((o) => o.id == b.dataset.est), vistaOrg)));
}

async function cambiarEstado(base, o, recargar) {
  try {
    if (!o.activo) { await api(`${base}/${o.id}/activar`, { method: "POST" }); aviso("Reactivado"); return recargar(); }
    await api(`${base}/${o.id}/desactivar`, { method: "POST" });
    aviso("Desactivado"); recargar();
  } catch (e) {
    if (e.datos?.dependientes) {
      const lista = e.datos.dependientes.map((d) => `<li>${esc(d.tipo)}: ${esc(d.codigo)}</li>`).join("");
      const cuerpo = dialogo(`<h2>Registros dependientes activos</h2><p>${esc(e.message)}</p><ul>${lista}</ul>
        <div class="acciones"><button class="peligro" id="c-si">Desactivar en cascada</button><button class="secundario" id="c-no">Cancelar</button></div>`);
      $("#c-no", cuerpo).onclick = cerrar;
      $("#c-si", cuerpo).onclick = async () => {
        const r = await api(`${base}/${o.id}/desactivar?cascada=true`, { method: "POST" });
        cerrar(); aviso(`Desactivados ${r.desactivados.length} registro(s)`); recargar();
      };
    } else aviso(e.message);
  }
}

// ------------------------------------------------------------------ usuarios
async function vistaUsuarios() {
  const [lista, puestos] = await Promise.all([api("/usuarios"), api("/org/puestos")]);
  $("#contenido").innerHTML = `<div class="panel">
    <div class="acciones">${esAdmin() ? '<button id="u-nuevo">Nuevo usuario</button>' : ""}</div>
    <table><thead><tr><th>Usuario</th><th>Nombre</th><th>Correo</th><th>Rol</th><th>Empresa / Sección / Puesto</th><th>Estado</th><th></th></tr></thead><tbody>
    ${lista.map((u) => `<tr><td>${esc(u.usuario)}</td><td>${esc(u.nombre)}</td><td>${esc(u.correo)}</td><td>${esc(u.rol)}</td>
      <td>${esc(u.organizacion.empresa.codigo)} / ${esc(u.organizacion.seccion.codigo)} / ${esc(u.organizacion.puesto.nombre)}</td>
      <td>${u.activo ? '<span class="etq ok">activo</span>' : '<span class="etq no">inactivo</span>'}</td>
      <td>${esAdmin() ? `<button class="mini secundario" data-ed="${u.id}">Editar</button>
        <button class="mini ${u.activo ? "peligro" : ""}" data-est="${u.id}">${u.activo ? "Desactivar" : "Activar"}</button>` : ""}</td></tr>`).join("")}
    </tbody></table></div>`;
  const editar = (u = null) => {
    const cuerpo = dialogo(`<h2>${u ? "Editar" : "Nuevo"} usuario</h2><form id="f-u">
      <label>Nombre <input name="nombre" required value="${esc(u?.nombre)}"></label>
      <label>Usuario <input name="usuario" required value="${esc(u?.usuario)}"></label>
      <label>Correo <input name="correo" type="email" value="${esc(u?.correo)}"></label>
      <label>Rol <select name="rol">${["consulta", "administrador"].map((r) => `<option ${u?.rol === r ? "selected" : ""}>${r}</option>`).join("")}</select></label>
      <label class="ancho">Puesto <select name="puesto_id" required>${opciones(puestos.filter((p) => p.activo || p.id === u?.puesto_id), u?.puesto_id, (p) => `${p.codigo} · ${p.nombre}`, "Seleccione…")}</select></label>
      <label class="ancho">Contraseña ${u ? "(dejar vacío para no cambiar)" : ""} <input name="password" type="password" minlength="8" ${u ? "" : "required"} autocomplete="new-password"></label>
      <p class="error ancho" id="u-err"></p>
      <div class="acciones ancho"><button>Guardar</button><button type="button" class="secundario" id="u-c">Cancelar</button></div></form>`);
    $("#u-c", cuerpo).onclick = cerrar;
    $("#f-u", cuerpo).onsubmit = async (e) => {
      e.preventDefault();
      const d = formDatos(e.target); if (!d.password) delete d.password;
      try { await api(u ? `/usuarios/${u.id}` : "/usuarios", { method: u ? "PUT" : "POST", body: d }); cerrar(); vistaUsuarios(); aviso("Usuario guardado"); }
      catch (err) { $("#u-err", cuerpo).textContent = err.message; }
    };
  };
  $("#u-nuevo")?.addEventListener("click", () => editar());
  document.querySelectorAll("[data-ed]").forEach((b) => (b.onclick = () => editar(lista.find((u) => u.id == b.dataset.ed))));
  document.querySelectorAll("[data-est]").forEach((b) => (b.onclick = () => cambiarEstado("/usuarios", lista.find((u) => u.id == b.dataset.est), vistaUsuarios)));
}

// ------------------------------------------------------------------ catálogos controlados
let catActual = "clases";
async function vistaCatalogos() {
  cache = {};
  const [lista, mapeos] = await Promise.all([api(`/catalogos/${catActual}`), api("/mapeos")]);
  const titulos = { clases: "Clases de servicio", criticidades: "Criticidades", tipos: "Tipos de servicio" };
  $("#contenido").innerHTML = `
    <div class="pestanas">${Object.entries(titulos).map(([k, t]) => `<button data-c="${k}" class="${k === catActual ? "activo" : ""}">${t}</button>`).join("")}</div>
    <div class="panel">
      ${esAdmin() ? '<form id="f-cat" class="acciones"><input name="nombre" placeholder="Nuevo valor" required style="max-width:260px"><button>Agregar</button></form>' : ""}
      <table><thead><tr><th>Valor</th><th>Estado</th><th></th></tr></thead><tbody>
      ${lista.map((o) => `<tr><td>${esc(o.nombre)}</td><td>${o.activo ? '<span class="etq ok">activo</span>' : '<span class="etq no">inactivo</span>'}</td>
        <td>${esAdmin() ? `<button class="mini ${o.activo ? "peligro" : ""}" data-t="${o.id}" data-a="${o.activo}">${o.activo ? "Desactivar" : "Activar"}</button>` : ""}</td></tr>`).join("")}
      </tbody></table></div>
    <div class="panel"><h3>Mapeo de etiquetas corregidas en la importación</h3>
      <table><thead><tr><th>Campo</th><th>Valor original</th><th>Valor de catálogo</th><th>Motivo</th></tr></thead><tbody>
      ${mapeos.map((m) => `<tr><td>${esc(m.campo)}</td><td>${esc(m.valor_original)}</td><td>${esc(m.valor_canonico)}</td><td>${esc(m.motivo)}</td></tr>`).join("") || '<tr><td colspan="4">Sin correcciones</td></tr>'}
      </tbody></table></div>`;
  document.querySelectorAll(".pestanas button").forEach((b) => (b.onclick = () => { catActual = b.dataset.c; vistaCatalogos(); }));
  $("#f-cat")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api(`/catalogos/${catActual}`, { method: "POST", body: formDatos(e.target) }); vistaCatalogos(); } catch (err) { aviso(err.message); }
  });
  document.querySelectorAll("[data-t]").forEach((b) => (b.onclick = async () => {
    const o = lista.find((x) => x.id == b.dataset.t);
    try { await api(`/catalogos/${catActual}/${o.id}`, { method: "PUT", body: { nombre: o.nombre, activo: !o.activo } }); vistaCatalogos(); } catch (err) { aviso(err.message); }
  }));
}

// ------------------------------------------------------------------ importación
async function vistaImportacion() {
  const lista = await api("/importaciones");
  $("#contenido").innerHTML = `<div class="panel">
    <div class="acciones">${esAdmin() ? '<button id="imp-run">Importar data/CatalogoServicios.xlsx</button>' : ""}
      <small>La importación es repetible: no duplica registros y respeta las ediciones hechas en la aplicación.</small></div>
    <table><thead><tr><th>#</th><th>Fecha</th><th>Archivo (SHA-256)</th><th>N1 / N2</th><th>N2 creados / actualizados / sin cambios</th><th>Observaciones</th><th></th></tr></thead><tbody>
    ${lista.map((i) => { const r = i.resumen; return `<tr><td>${i.id}</td><td>${esc(new Date(i.ejecutada_en).toLocaleString())}</td>
      <td>${esc(i.archivo)}<br><small>${esc(i.sha256.slice(0, 16))}…</small></td>
      <td><span class="etq ${r.control.ok ? "ok" : "no"}">${r.control.n1_encontrados} / ${r.control.n2_encontrados}</span></td>
      <td>${r.n2.creados} / ${r.n2.actualizados} / ${r.n2.sin_cambios}</td><td>${r.observaciones}</td>
      <td><button class="mini secundario" data-obs="${i.id}">Ver</button></td></tr>`; }).join("") || '<tr><td colspan="7">Aún no se ha importado</td></tr>'}
    </tbody></table></div><div id="obs"></div>`;
  $("#imp-run")?.addEventListener("click", async () => {
    try { const r = await api("/importaciones", { method: "POST" }); aviso(`Importación #${r.importacion_id}: ${r.control.n1_encontrados} N1 / ${r.control.n2_encontrados} N2`); cache = {}; vistaImportacion(); }
    catch (e) { aviso(e.message); }
  });
  document.querySelectorAll("[data-obs]").forEach((b) => (b.onclick = async () => {
    const obs = await api(`/importaciones/${b.dataset.obs}/observaciones`);
    $("#obs").innerHTML = `<div class="panel"><h3>Observaciones de la importación #${esc(b.dataset.obs)}</h3>
      <table><thead><tr><th>Tipo</th><th>Rango</th><th>Código</th><th>Mensaje</th></tr></thead><tbody>
      ${obs.map((o) => `<tr><td>${esc(o.tipo)}</td><td>${esc(o.rango)}</td><td>${esc(o.codigo)}</td><td>${esc(o.mensaje)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }));
}

iniciar();
