#include <Arduino.h>
#include <WiFi.h>
#include <SPIFFS.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <ArduinoJson.h>

AsyncWebServer server(80);
Preferences prefs;

// ── Wi-Fi connect ─────────────────────────────────────────────────────────────

void connectWiFi() {
  prefs.begin("esphost", true);
  String ssid = prefs.getString("ssid", "");
  String pass = prefs.getString("pass", "");
  prefs.end();

  if (ssid.isEmpty()) {
    Serial.println("[ESPHOST] No Wi-Fi credentials saved.");
    Serial.println("[ESPHOST] Send: {\"cmd\":\"setwifi\",\"ssid\":\"NAME\",\"pass\":\"PASS\"}");
    return;
  }

  Serial.printf("[ESPHOST] Connecting to %s", ssid.c_str());
  WiFi.begin(ssid.c_str(), pass.c_str());

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[ESPHOST] Connected.");
    Serial.printf("[ESPHOST] IP: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("[ESPHOST] READY ip=%s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[ESPHOST] Wi-Fi failed. Check credentials.");
  }
}

// ── Serial command handler ─────────────────────────────────────────────────────

void handleSerial() {
  if (!Serial.available()) return;

  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.isEmpty()) return;

  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) return;

  String cmd = doc["cmd"] | "";

  if (cmd == "setwifi") {
    String ssid = doc["ssid"] | "";
    String pass = doc["pass"] | "";
    prefs.begin("esphost", false);
    prefs.putString("ssid", ssid);
    prefs.putString("pass", pass);
    prefs.end();
    Serial.println("[ESPHOST] Wi-Fi saved. Restarting...");
    delay(500);
    ESP.restart();
  }

  else if (cmd == "info") {
    StaticJsonDocument<256> resp;
    resp["free_ram"]    = ESP.getFreeHeap();
    resp["flash_size"]  = ESP.getFlashChipSize();
    resp["cpu_freq"]    = ESP.getCpuFreqMHz();
    resp["ip"]          = WiFi.localIP().toString();
    resp["ssid"]        = WiFi.SSID();
    serializeJson(resp, Serial);
    Serial.println();
  }

  else if (cmd == "ping") {
    Serial.println("[ESPHOST] pong");
  }
}

// ── Web server routes ──────────────────────────────────────────────────────────

void setupServer() {
  if (!SPIFFS.begin(true)) {
    Serial.println("[ESPHOST] SPIFFS mount failed");
    return;
  }

  // Serve all static files from SPIFFS root
  server.serveStatic("/", SPIFFS, "/").setDefaultFile("index.html");

  // 404 handler
  server.onNotFound([](AsyncWebServerRequest *req) {
    if (SPIFFS.exists("/404.html")) {
      req->send(SPIFFS, "/404.html", "text/html");
    } else {
      req->send(404, "text/plain", "Not found");
    }
  });

  // Health check endpoint — used by tunnel keepalive
  server.on("/esphost-health", HTTP_GET, [](AsyncWebServerRequest *req) {
    StaticJsonDocument<128> doc;
    doc["status"]   = "ok";
    doc["free_ram"] = ESP.getFreeHeap();
    doc["uptime"]   = millis() / 1000;
    String out;
    serializeJson(doc, out);
    req->send(200, "application/json", out);
  });

  server.begin();
  Serial.println("[ESPHOST] HTTP server started on port 80");
}

// ── Setup & loop ───────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("[ESPHOST] Booting...");

  connectWiFi();
  setupServer();
}

void loop() {
  handleSerial();
  delay(10);
}
