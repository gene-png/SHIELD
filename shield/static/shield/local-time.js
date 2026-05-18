// Round-7 §18: convert every `<time datetime="..." class="local-time">`
// element in the document to the viewer's local-time rendering.
//
// Storage + audit + wire stay UTC. This file is purely a display
// layer. The fallback text (rendered server-side by the `local_time`
// Jinja filter) is a pretty UTC string — visible if the JS is disabled
// or blocked. Once this script runs, each element's textContent gets
// the locale-friendly string.
//
// The script is intentionally idempotent: running it twice is safe;
// it just rewrites textContent again.

(function () {
  'use strict';

  function format(date, fmt) {
    if (isNaN(date.getTime())) return null;
    if (fmt === 'date') {
      return date.toLocaleDateString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
      });
    }
    return date.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit',
      timeZoneName: 'short',
    });
  }

  function convert() {
    var nodes = document.querySelectorAll('time.local-time');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var iso = el.getAttribute('datetime');
      if (!iso) continue;
      var d = new Date(iso);
      var pretty = format(d, el.getAttribute('data-fmt') || 'datetime');
      if (pretty) {
        el.textContent = pretty;
        // Hand the title a UTC value so hovering reveals the source.
        el.setAttribute('title', iso);
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', convert);
  } else {
    convert();
  }
})();
