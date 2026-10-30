/* start-up: runs after engine.js and every page file */
fillConfig();
setReveal(true);   /* what happened is shown by default (2026-10-27); 'Plan only' hides it */
go((location.hash || "#" + HOME).slice(1));
})();
