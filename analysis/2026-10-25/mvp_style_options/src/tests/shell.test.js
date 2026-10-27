/* The shell: four pages behind two assets, settings behind the gear, no operator chrome. */
ok("four page buttons", $$(".tb-nav .nav").map(function(b){ return b.getAttribute("data-go"); }).join() === "plan,evidence,calls,value");
ok("no run status / save / export", !$("#run") && !$("#pri") && !$("#exp-all"));
ok("gear opens settings", (goTo("config"), $("#s-config").classList.contains("on")));
["plan", "evidence", "calls", "value"].forEach(function(p){
  goTo(p);
  var on = $$(".screen.on");
  ok("page " + p + " opens alone", on.length === 1 && on[0].id === "s-" + p, on.map(function(s){ return s.id; }).join());
  ok("page " + p + " has a title and context", txt("#ph-title").length > 3 && txt("#ph-ctx").length > 3, txt("#ph-title") + " | " + txt("#ph-ctx"));
  ok("page " + p + " shows its asset's nav only", $$(".tb-nav.pages.on").length === 1 && $$(".tb-nav.pages.on .nav").some(function(b){ return b.getAttribute("data-go") === p; }));
  ok("page " + p + " nav highlights", $$(".nav.on").length === 1 && $(".nav.on").getAttribute("data-go") === p);
});
goTo("plan");
done();
