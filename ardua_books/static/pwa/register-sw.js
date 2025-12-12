if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker
        .register("/static/pwa/service-worker.js")
        .catch(function (err) {
          console.log("SW registration failed:", err);
        });
    });
  }
  