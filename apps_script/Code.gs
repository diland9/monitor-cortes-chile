/**
 * Monitor cortes SEC - Google Apps Script
 * Corre en servidores de Google (la SEC bloquea las IPs de GitHub Actions).
 *
 * Uso: crear una Google Sheet, Extensiones > Apps Script, pegar este archivo.
 *   1. Ejecutar probarSEC()  -> confirma que la SEC responde desde Google.
 *   2. Ejecutar instalar()   -> crea el disparador cada hora y hace la 1a captura.
 *   3. Implementar > Nueva implementacion > Aplicacion web
 *      (ejecutar como: yo / acceso: cualquier persona) y copiar la URL /exec
 *      en DATA_URL de docs/index.html.
 */
var BASE = 'https://apps.sec.cl/INTONLINEv1/ClientesAfectados/';
var HEADERS = {
  'X-Requested-With': 'XMLHttpRequest',
  'Referer': 'https://apps.sec.cl/INTONLINEv1/index.aspx',
  'Origin': 'https://apps.sec.cl'
};

function post_(endpoint, body) {
  var res = UrlFetchApp.fetch(BASE + endpoint, {
    method: 'post',
    contentType: 'application/json; charset=UTF-8',
    headers: HEADERS,
    payload: body ? JSON.stringify(body) : '',
    muteHttpExceptions: true
  });
  if (res.getResponseCode() !== 200) {
    throw new Error(endpoint + ' -> HTTP ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 200));
  }
  return JSON.parse(res.getContentText());
}

function probarSEC() {
  Logger.log(JSON.stringify(post_('GetHoraServer')));
  Logger.log(JSON.stringify(post_('GetClientesNacional')));
}

function capturar_() {
  var ts = post_('GetHoraServer')[0].FECHA;                 // "dd/mm/yyyy HH:MM" hora Chile
  var totalPais = Number(post_('GetClientesNacional')[0].CLIENTES);
  var serie = post_('Get');                                  // [{anho,mes,dia,hora,clientes_afectados}]
  var u = serie[serie.length - 1];
  var det = post_('GetPorFecha', {anho: u.anho, mes: u.mes, dia: u.dia, hora: u.hora});

  var regiones = {}, comunas = [];
  var total = 0;
  det.forEach(function (r) {
    var n = Number(r.CLIENTES_AFECTADOS) || 0;
    total += n;
    regiones[r.NOMBRE_REGION] = (regiones[r.NOMBRE_REGION] || 0) + n;
    comunas.push({region: r.NOMBRE_REGION, comuna: String(r.NOMBRE_COMUNA).trim(), afectados: n});
  });
  comunas.sort(function (a, b) { return b.afectados - a.afectados; });
  var regList = Object.keys(regiones).map(function (k) { return {region: k, afectados: regiones[k]}; })
    .sort(function (a, b) { return b.afectados - a.afectados; });

  function p2(x) { return ('0' + x).slice(-2); }
  return {
    actualizado: ts,
    hora_datos: p2(u.dia) + '/' + p2(u.mes) + '/' + u.anho + ' ' + p2(u.hora) + ':00',
    total_afectados: total,
    total_clientes_pais: totalPais,
    pct_sin_suministro: totalPais ? Math.round(total / totalPais * 1e6) / 1e4 : null,
    serie_7d: serie.map(function (s) {
      return {t: s.anho + '-' + p2(s.mes) + '-' + p2(s.dia) + 'T' + p2(s.hora) + ':00', v: Number(s.clientes_afectados)};
    }),
    regiones: regList,
    comunas: comunas.slice(0, 50),
    _comunasTodas: comunas
  };
}

function hoja_(nombre, cabecera) {
  var ss = SpreadsheetApp.getActive();
  var sh = ss.getSheetByName(nombre) || ss.insertSheet(nombre);
  if (sh.getLastRow() === 0) sh.appendRow(cabecera);
  return sh;
}

function capturaHoraria() {
  var d = capturar_();
  var nac = hoja_('nacional', ['captured_at', 'hora_datos', 'total_afectados', 'total_clientes_pais', 'pct_sin_suministro']);
  // evita duplicar si la misma captura ya existe
  if (nac.getLastRow() > 1 && String(nac.getRange(nac.getLastRow(), 1).getValue()) === d.actualizado) return;
  nac.appendRow([d.actualizado, d.hora_datos, d.total_afectados, d.total_clientes_pais, d.pct_sin_suministro]);

  var reg = hoja_('region', ['captured_at', 'region', 'afectados']);
  d.regiones.forEach(function (r) { reg.appendRow([d.actualizado, r.region, r.afectados]); });
  var com = hoja_('comuna', ['captured_at', 'region', 'comuna', 'afectados']);
  var filas = d._comunasTodas.map(function (c) { return [d.actualizado, c.region, c.comuna, c.afectados]; });
  if (filas.length) com.getRange(com.getLastRow() + 1, 1, filas.length, 4).setValues(filas);

  // historial de capturas para el dashboard (ultimas 500)
  var vals = nac.getDataRange().getValues().slice(1).slice(-500);
  d.capturas = vals.map(function (r) { return {t: String(r[0]), v: Number(r[2])}; });
  delete d._comunasTodas;
  PropertiesService.getScriptProperties().setProperty('estado', JSON.stringify(d));
}

function doGet() {
  var s = PropertiesService.getScriptProperties().getProperty('estado') || '{}';
  return ContentService.createTextOutput(s).setMimeType(ContentService.MimeType.JSON);
}

function instalar() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('capturaHoraria').timeBased().everyHours(1).create();
  capturaHoraria();
}
