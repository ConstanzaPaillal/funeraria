/*
 * Cristo Rey · navegación y acciones sin recargar toda la página (AJAX).
 *
 * Mejora progresiva: sin JavaScript, cada enlace y formulario sigue funcionando como página normal.
 * Con JavaScript, se pide la página por fetch y se reemplaza solo el contenido, así el menú, el encabezado
 * y la posición de lectura no "saltan". Los archivos (Excel, PDF) y el ingreso/salida se descargan normal.
 */
(function () {
  'use strict';
  if (!window.fetch || !window.DOMParser || !window.history.pushState) return;

  var EXCLUIR = /\/(exportar|pdf|admin|login|logout|media|static)(\/|$)/;
  var barra, vivo, solicitudGet = null, temporizador = null;

  function iniciar() {
    barra = document.createElement('div'); barra.id = 'cr-barra'; barra.setAttribute('aria-hidden', 'true');
    document.body.appendChild(barra);
    vivo = document.getElementById('cr-live');
  }

  function ocupado(si) {
    document.body.setAttribute('aria-busy', si ? 'true' : 'false');
    barra.className = '';
    if (si) { void barra.offsetWidth; barra.className = 'on'; }
    else { barra.className = 'fin'; setTimeout(function () { if (barra.className === 'fin') barra.className = ''; }, 600); }
  }

  function esDelSitio(url) {
    try { var u = new URL(url, location.href); return u.origin === location.origin && !EXCLUIR.test(u.pathname); }
    catch (e) { return false; }
  }

  function anunciar(texto) { if (vivo && texto) { vivo.textContent = ''; setTimeout(function () { vivo.textContent = texto; }, 50); } }

  function mostrarError(texto) {
    var main = document.getElementById('main'); if (!main) return;
    var viejo = document.getElementById('cr-error'); if (viejo) viejo.remove();
    var d = document.createElement('div'); d.id = 'cr-error'; d.className = 'aviso aviso--crit'; d.setAttribute('role', 'alert');
    var p = document.createElement('p'); p.textContent = texto; var w = document.createElement('div'); w.appendChild(p); d.appendChild(w);
    main.insertBefore(d, main.firstChild);
    anunciar(texto);
  }

  // Reemplaza el contenido de la página con el de la respuesta. Devuelve false si la respuesta no es una página completa.
  function reemplazar(html, urlFinal, opciones) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    if (!doc.getElementById('main')) return false;
    var activo = document.activeElement, idActivo = activo && activo.id, caret = null;
    if (idActivo && activo.setSelectionRange && /^(text|search)$/.test(activo.type || '')) caret = [activo.selectionStart, activo.selectionEnd];
    var y = window.scrollY;
    var hijos = Array.prototype.slice.call(document.body.children);
    hijos.forEach(function (n) { if (n.tagName !== 'SCRIPT' && n.id !== 'cr-barra' && n.id !== 'cr-live') n.remove(); });
    Array.prototype.slice.call(doc.body.children).forEach(function (n) {
      if (n.tagName !== 'SCRIPT' && n.id !== 'cr-live') document.body.insertBefore(document.adoptNode(n), barra);
    });
    document.title = doc.title || document.title;
    var igual = opciones.mismaPagina;
    window.scrollTo(0, igual ? y : 0);
    // Foco: si el campo seguía existiendo (búsqueda en vivo), se devuelve; si no, al encabezado o al contenido.
    var nuevo = idActivo && document.getElementById(idActivo);
    if (nuevo && opciones.conservarFoco) { nuevo.focus({ preventScroll: true }); if (caret) { try { nuevo.setSelectionRange(caret[0], caret[1]); } catch (e) {} } }
    else if (!opciones.sinFoco) { var h = document.querySelector('#main h1') || document.getElementById('main'); h.setAttribute('tabindex', '-1'); h.focus({ preventScroll: true }); }
    var aviso = document.querySelector('#main .avisos'); anunciar(aviso ? aviso.textContent.trim() : (opciones.anuncio || ''));
    autoenviar();
    return true;
  }

  // Formularios con data-autoenviar (la pasarela de pago) se envían solos; el botón queda por si no ocurre.
  function autoenviar() {
    var f = document.querySelector('form[data-autoenviar]');
    if (f && !f.dataset.enviado) { f.dataset.enviado = '1'; setTimeout(function () { f.submit(); }, 400); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', autoenviar); else autoenviar();

  function mismaRuta(a, b) { var u = new URL(a, location.href), v = new URL(b, location.href); return u.pathname === v.pathname; }

  function pedir(url, init, opciones) {
    opciones = opciones || {};
    var antes = location.href;
    var ctl = null;
    if (!init.method || init.method === 'GET') { if (solicitudGet) solicitudGet.abort(); ctl = solicitudGet = new AbortController(); init.signal = ctl.signal; }
    init.credentials = 'same-origin';
    init.headers = Object.assign({ 'X-Requested-With': 'fetch' }, init.headers || {});
    ocupado(true);
    return fetch(url, init).then(function (res) {
      var tipo = res.headers.get('content-type') || '';
      if (tipo.indexOf('text/html') === -1) { ocupado(false); window.location.assign(res.url || url); return; }
      return res.text().then(function (html) {
        var final = res.url || url;
        var misma = mismaRuta(antes, final);
        if (res.status >= 500) { ocupado(false); mostrarError('No se pudo completar la acción. Inténtalo de nuevo en unos minutos.'); return; }
        if (!reemplazar(html, final, Object.assign({ mismaPagina: misma }, opciones))) { ocupado(false); window.location.assign(final); return; }
        if (opciones.push !== false) { if (final !== antes) history.pushState({ cr: 1 }, '', final); }
        ocupado(false);
      });
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;
      ocupado(false);
      mostrarError('No hay conexión con el servidor. Revisa tu internet e inténtalo de nuevo.');
    });
  }

  // --- Enlaces ---------------------------------------------------------------
  document.addEventListener('click', function (e) {
    var t = e.target;
    var abrir = t.closest && t.closest('.toggle');
    if (abrir) {   // filas desplegables del listado
      var abierto = abrir.getAttribute('aria-expanded') === 'true';
      var fila = document.getElementById(abrir.getAttribute('aria-controls'));
      if (fila) fila.hidden = abierto;
      abrir.setAttribute('aria-expanded', String(!abierto));
      abrir.textContent = abierto ? abrir.dataset.abrir : abrir.dataset.cerrar;
      return;
    }
    if (t.closest && t.closest('[data-imprimir]')) { window.print(); return; }
    var a = t.closest && t.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target && a.target !== '_self') return;
    if (a.hasAttribute('download') || a.hasAttribute('data-no-ajax') || a.closest('[data-no-ajax]')) return;
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#' || /^(mailto:|tel:)/.test(href) || !esDelSitio(a.href)) return;
    e.preventDefault();
    pedir(a.href, {});
  });

  // --- Formularios -----------------------------------------------------------
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!(f instanceof HTMLFormElement) || e.defaultPrevented) return;
    var conf = f.getAttribute('data-confirmar');
    if (conf && !window.confirm(conf)) { e.preventDefault(); return; }
    if (f.hasAttribute('data-no-ajax') || (f.target && f.target !== '_self')) return;
    var accion = f.getAttribute('action') || location.href;
    if (!esDelSitio(accion)) return;
    e.preventDefault();
    var boton = e.submitter;
    var datos = new FormData(f, boton || undefined);
    var metodo = (f.getAttribute('method') || 'GET').toUpperCase();
    var enBusqueda = f.getAttribute('role') === 'search';
    if (boton && !enBusqueda) boton.disabled = true;   // evita el doble envío
    if (metodo === 'GET') {
      var u = new URL(accion, location.href), q = new URLSearchParams();
      datos.forEach(function (v, k) { if (typeof v === 'string' && v !== '') q.append(k, v); });
      u.search = q.toString();
      pedir(u.toString(), {}, { conservarFoco: enBusqueda });
    } else {
      pedir(accion, { method: 'POST', body: datos }).then(function () { if (boton && document.contains(boton)) boton.disabled = false; });
    }
  });

  // --- Búsqueda en vivo: escribir o cambiar un filtro actualiza los resultados ---
  document.addEventListener('input', function (e) {
    var c = e.target;
    var f = c.closest && c.closest('form[role="search"]');
    if (!f || !/^(search|text)$/.test(c.type || '')) return;
    clearTimeout(temporizador);
    temporizador = setTimeout(function () { if (f.requestSubmit) f.requestSubmit(); }, 350);
  });
  document.addEventListener('change', function (e) {
    var c = e.target, f = c.closest && c.closest('form[role="search"]');
    if (f && (c.tagName === 'SELECT' || c.type === 'date') && f.requestSubmit) f.requestSubmit();
  });

  // --- Botones atrás/adelante ---
  window.addEventListener('popstate', function () { pedir(location.href, {}, { push: false }); });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar); else iniciar();
})();
