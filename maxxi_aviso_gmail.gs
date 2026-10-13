/**
 * Aviso de Gmail para Maxxi (Google Apps Script, cuenta accounting@katapulk.com).
 *
 * Cada 5 minutos mira si llegaron correos nuevos al inbox y, si hay, despierta a la rutina
 * Maxxi en Claude (disparador API) pasándole los IDs de esos correos. Maxxi decide si te
 * corresponden y deja el borrador. Este script solo LEE el correo: no envía ni cambia nada.
 *
 * Instalación (una sola vez):
 *  1. script.google.com → Nuevo proyecto → pega este código → Guardar.
 *  2. Configuración del proyecto (⚙️) → Propiedades de la secuencia de comandos → agrega:
 *       MAXXI_URL   = la URL del disparador API de la rutina (termina en /fire)
 *       MAXXI_TOKEN = el token que te dio "Generate token"
 *  3. Elige la función "instalar" y dale ▶ Ejecutar (acepta los permisos de Gmail).
 */

const CUENTA = 'accounting@katapulk.com';
// No despierta a Maxxi por publicidad, redes sociales ni foros.
const FILTRO = 'in:inbox -from:' + CUENTA + ' -category:promotions -category:social -category:forums';

function revisarCorreoNuevo() {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('MAXXI_URL');
  const token = props.getProperty('MAXXI_TOKEN');
  if (!url || !token) throw new Error('Faltan MAXXI_URL o MAXXI_TOKEN en las propiedades del script.');

  const ahora = Math.floor(Date.now() / 1000);
  const desde = Number(props.getProperty('ULTIMA_REVISION') || ahora - 600);

  const ids = [];
  GmailApp.search(FILTRO + ' after:' + desde, 0, 50).forEach(function (hilo) {
    hilo.getMessages().forEach(function (m) {
      const t = Math.floor(m.getDate().getTime() / 1000);
      if (t > desde && m.getFrom().toLowerCase().indexOf(CUENTA) === -1 && !m.isInTrash()) ids.push(m.getId());
    });
  });
  if (ids.length === 0) {
    props.setProperty('ULTIMA_REVISION', String(ahora));
    return;
  }

  const resp = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      'Authorization': 'Bearer ' + token,
      'anthropic-beta': 'experimental-cc-routine-2026-04-01',
      'anthropic-version': '2023-06-01'
    },
    payload: JSON.stringify({ text: 'CORREOS_NUEVOS: ' + ids.join(',') }),
    muteHttpExceptions: true
  });
  // Solo se avanza la marca de tiempo si Claude recibió el aviso; si falló, se reintenta en 5 minutos.
  if (resp.getResponseCode() < 300) {
    props.setProperty('ULTIMA_REVISION', String(ahora));
  } else {
    console.error('Maxxi no recibió el aviso: ' + resp.getResponseCode() + ' ' + resp.getContentText().slice(0, 300));
  }
}

/** Ejecutar una vez: crea el reloj de cada 5 minutos (y borra uno anterior si existía). */
function instalar() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'revisarCorreoNuevo') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('revisarCorreoNuevo').timeBased().everyMinutes(5).create();
  PropertiesService.getScriptProperties().setProperty('ULTIMA_REVISION', String(Math.floor(Date.now() / 1000)));
}

/** Para apagarlo: ejecuta esta función. */
function desinstalar() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
}
