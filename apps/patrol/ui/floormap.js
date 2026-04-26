(function (global) {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";

  var DEFAULTS = {
    width: 640,
    height: 380,
    minX: -10,
    maxX: 20,
    minY: -10,
    maxY: 20,
    title: "Patrol Floor Map"
  };

  function create(container, options) {
    return new PatrolFloorMap(resolveContainer(container), options || {});
  }

  function resolveContainer(container) {
    if (typeof container === "string") {
      var element = document.querySelector(container);
      if (!element) {
        throw new Error("PatrolFloorMap container not found: " + container);
      }
      return element;
    }
    if (container && container.nodeType === 1) {
      return container;
    }
    throw new Error("PatrolFloorMap container must be a selector or DOM element.");
  }

  function svgElement(tagName, attributes) {
    var element = document.createElementNS(SVG_NS, tagName);
    Object.keys(attributes || {}).forEach(function (key) {
      element.setAttribute(key, String(attributes[key]));
    });
    return element;
  }

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function safeObject(value) {
    return value && typeof value === "object" ? value : {};
  }

  function normalizePosition(position) {
    if (Array.isArray(position) && position.length >= 2) {
      var xList = Number(position[0]);
      var yList = Number(position[1]);
      var zList = position.length > 2 ? Number(position[2]) : 0;
      if (Number.isFinite(xList) && Number.isFinite(yList)) {
        return { x: xList, y: yList, z: Number.isFinite(zList) ? zList : 0 };
      }
      return null;
    }

    if (position && typeof position === "object") {
      var xObj = Number(position.x);
      var yObj = Number(position.y);
      var zObj = "z" in position ? Number(position.z) : 0;
      if (Number.isFinite(xObj) && Number.isFinite(yObj)) {
        return { x: xObj, y: yObj, z: Number.isFinite(zObj) ? zObj : 0 };
      }
    }

    return null;
  }

  function formatCoord(value) {
    return Number(value).toFixed(2);
  }

  function PatrolFloorMap(container, options) {
    this.container = container;
    this.options = Object.assign({}, DEFAULTS, options || {});
    this.routes = [];
    this.zones = [];
    this.zoneAnchors = {};
    this.anomalyMarkers = {};
    this.destroyed = false;
    this._render();
  }

  PatrolFloorMap.prototype._render = function () {
    var width = this.options.width;
    var height = this.options.height;
    var padding = { top: 42, right: 26, bottom: 44, left: 28 };
    this.padding = padding;
    this.plotWidth = width - padding.left - padding.right;
    this.plotHeight = height - padding.top - padding.bottom;

    this.container.innerHTML = "";

    this.root = document.createElement("div");
    this.root.className = "patrol-floormap-root";
    this.root.style.width = "100%";
    this.root.style.minHeight = String(height) + "px";

    this.svg = svgElement("svg", {
      viewBox: "0 0 " + width + " " + height,
      width: "100%",
      height: "100%",
      role: "img",
      "aria-label": this.options.title
    });
    this.svg.style.display = "block";

    this.svg.appendChild(svgElement("rect", {
      x: 0,
      y: 0,
      width: width,
      height: height,
      rx: 24,
      fill: "#0f161f"
    }));

    var title = svgElement("text", {
      x: padding.left,
      y: 26,
      fill: "#e7f0f8",
      "font-size": 16,
      "font-weight": 700,
      "letter-spacing": 0.7
    });
    title.textContent = this.options.title;
    this.svg.appendChild(title);

    this.plot = svgElement("g", {
      transform: "translate(" + padding.left + " " + padding.top + ")"
    });
    this.svg.appendChild(this.plot);

    this.plot.appendChild(svgElement("rect", {
      x: 0,
      y: 0,
      width: this.plotWidth,
      height: this.plotHeight,
      rx: 18,
      fill: "#13202c",
      stroke: "#355063",
      "stroke-width": 1.4
    }));

    this.gridLayer = svgElement("g");
    this.routeLayer = svgElement("g");
    this.zoneLayer = svgElement("g");
    this.robotLayer = svgElement("g");
    this.anomalyLayer = svgElement("g");
    this.labelLayer = svgElement("g");

    this._renderGrid();
    this.plot.appendChild(this.gridLayer);
    this.plot.appendChild(this.routeLayer);
    this.plot.appendChild(this.zoneLayer);
    this.plot.appendChild(this.anomalyLayer);
    this.plot.appendChild(this.robotLayer);
    this.plot.appendChild(this.labelLayer);

    this.robotGlow = svgElement("circle", {
      cx: this._mapX(0),
      cy: this._mapY(0),
      r: 10,
      fill: "rgba(97, 201, 255, 0.16)"
    });
    this.robotDot = svgElement("circle", {
      cx: this._mapX(0),
      cy: this._mapY(0),
      r: 4.5,
      fill: "#73dbff",
      stroke: "#eef8ff",
      "stroke-width": 1.2
    });
    this.robotLayer.appendChild(this.robotGlow);
    this.robotLayer.appendChild(this.robotDot);

    this.statusLabel = svgElement("text", {
      x: padding.left,
      y: height - 14,
      fill: "#9fb9cb",
      "font-size": 12
    });
    this.statusLabel.textContent = "Map ready";
    this.svg.appendChild(this.statusLabel);

    this.root.appendChild(this.svg);
    this.container.appendChild(this.root);
  };

  PatrolFloorMap.prototype._renderGrid = function () {
    this.gridLayer.innerHTML = "";
    var columns = 6;
    var rows = 5;
    var index;

    for (index = 1; index < columns; index += 1) {
      var x = (this.plotWidth / columns) * index;
      this.gridLayer.appendChild(svgElement("line", {
        x1: x,
        y1: 0,
        x2: x,
        y2: this.plotHeight,
        stroke: "rgba(178, 208, 225, 0.08)",
        "stroke-width": 1
      }));
    }

    for (index = 1; index < rows; index += 1) {
      var y = (this.plotHeight / rows) * index;
      this.gridLayer.appendChild(svgElement("line", {
        x1: 0,
        y1: y,
        x2: this.plotWidth,
        y2: y,
        stroke: "rgba(178, 208, 225, 0.08)",
        "stroke-width": 1
      }));
    }
  };

  PatrolFloorMap.prototype._mapX = function (value) {
    var ratio = (value - this.options.minX) / (this.options.maxX - this.options.minX);
    return clamp(ratio, 0, 1) * this.plotWidth;
  };

  PatrolFloorMap.prototype._mapY = function (value) {
    var ratio = (value - this.options.minY) / (this.options.maxY - this.options.minY);
    return this.plotHeight - clamp(ratio, 0, 1) * this.plotHeight;
  };

  PatrolFloorMap.prototype.setRoutes = function (routes) {
    this.routes = Array.isArray(routes) ? routes.slice() : [];
    this._rebuildAnchors();
    this._renderRoutes();
    this._renderZones();
    this._renderAnomalies();
  };

  PatrolFloorMap.prototype.setZones = function (zones) {
    this.zones = Array.isArray(zones) ? zones.slice() : [];
    this._renderZones();
    this._renderAnomalies();
  };

  PatrolFloorMap.prototype.updateRobotPosition = function (position) {
    if (this.destroyed) {
      return;
    }
    var normalized = normalizePosition(position);
    if (!normalized) {
      return;
    }

    var cx = this._mapX(normalized.x);
    var cy = this._mapY(normalized.y);
    this.robotGlow.setAttribute("cx", String(cx));
    this.robotGlow.setAttribute("cy", String(cy));
    this.robotDot.setAttribute("cx", String(cx));
    this.robotDot.setAttribute("cy", String(cy));
    this.updateStatus("Robot at " + formatCoord(normalized.x) + ", " + formatCoord(normalized.y));
  };

  PatrolFloorMap.prototype.markAnomaly = function (zoneId, severity) {
    if (!zoneId) {
      return;
    }
    this.anomalyMarkers[String(zoneId)] = severity || "warning";
    this._renderAnomalies();
    if (!this.zoneAnchors[String(zoneId)]) {
      this.updateStatus("Anomaly reported for unknown zone " + zoneId);
    }
  };

  PatrolFloorMap.prototype.clearAnomaly = function (zoneId) {
    delete this.anomalyMarkers[String(zoneId)];
    this._renderAnomalies();
  };

  PatrolFloorMap.prototype.updateStatus = function (text) {
    if (this.destroyed || !this.statusLabel) {
      return;
    }
    this.statusLabel.textContent = text || "Ready";
  };

  PatrolFloorMap.prototype.destroy = function () {
    if (this.destroyed) {
      return;
    }
    this.destroyed = true;
    if (this.container) {
      this.container.innerHTML = "";
    }
  };

  PatrolFloorMap.prototype._rebuildAnchors = function () {
    this.zoneAnchors = {};
    var self = this;

    this.routes.forEach(function (route) {
      var waypoints = Array.isArray(route && route.waypoints) ? route.waypoints : [];
      waypoints.forEach(function (waypoint) {
        var metadata = safeObject(waypoint.metadata);
        if (metadata.observe === true && typeof metadata.zone_id === "string" && metadata.zone_id) {
          self.zoneAnchors[metadata.zone_id] = {
            x: Number(waypoint.x),
            y: Number(waypoint.y),
            routeId: route.id,
            waypointName: waypoint.name
          };
        }
      });
    });
  };

  PatrolFloorMap.prototype._renderRoutes = function () {
    this.routeLayer.innerHTML = "";
    this.labelLayer.innerHTML = "";
    var self = this;

    this.routes.forEach(function (route) {
      var waypoints = Array.isArray(route && route.waypoints) ? route.waypoints : [];
      if (!waypoints.length) {
        return;
      }

      var points = waypoints
        .filter(function (waypoint) {
          return isFiniteNumber(Number(waypoint.x)) && isFiniteNumber(Number(waypoint.y));
        })
        .map(function (waypoint) {
          return self._mapX(Number(waypoint.x)) + "," + self._mapY(Number(waypoint.y));
        })
        .join(" ");

      if (points) {
        self.routeLayer.appendChild(svgElement("polyline", {
          points: points,
          fill: "none",
          stroke: "rgba(104, 188, 246, 0.82)",
          "stroke-width": 2.4,
          "stroke-linecap": "round",
          "stroke-linejoin": "round"
        }));
      }

      waypoints.forEach(function (waypoint) {
        var x = Number(waypoint.x);
        var y = Number(waypoint.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
          return;
        }

        var pointX = self._mapX(x);
        var pointY = self._mapY(y);
        self.routeLayer.appendChild(svgElement("circle", {
          cx: pointX,
          cy: pointY,
          r: 3.2,
          fill: "#d5f4ff"
        }));

        var metadata = safeObject(waypoint.metadata);
        if (metadata.observe === true && typeof metadata.zone_id === "string" && metadata.zone_id) {
          var label = svgElement("text", {
            x: pointX + 7,
            y: pointY - 7,
            fill: "#d7c89b",
            "font-size": 11,
            "font-weight": 600
          });
          label.textContent = metadata.zone_id;
          self.labelLayer.appendChild(label);
        }
      });
    });
  };

  PatrolFloorMap.prototype._renderZones = function () {
    this.zoneLayer.innerHTML = "";
    var self = this;

    this.zones.forEach(function (zone) {
      var anchor = self.zoneAnchors[zone.zone_id];
      if (!anchor || !Number.isFinite(anchor.x) || !Number.isFinite(anchor.y)) {
        return;
      }

      var x = self._mapX(anchor.x);
      var y = self._mapY(anchor.y);

      self.zoneLayer.appendChild(svgElement("circle", {
        cx: x,
        cy: y,
        r: 11,
        fill: "rgba(238, 201, 104, 0.09)",
        stroke: "rgba(238, 201, 104, 0.45)",
        "stroke-width": 1.2
      }));
    });
  };

  PatrolFloorMap.prototype._renderAnomalies = function () {
    this.anomalyLayer.innerHTML = "";
    var self = this;

    Object.keys(this.anomalyMarkers).forEach(function (zoneId) {
      var anchor = self.zoneAnchors[zoneId];
      if (!anchor) {
        return;
      }

      var severity = self.anomalyMarkers[zoneId];
      var color = "#f0c15b";
      if (severity === "critical") {
        color = "#ff6d53";
      } else if (severity === "info") {
        color = "#6cd1ff";
      }

      var x = self._mapX(anchor.x);
      var y = self._mapY(anchor.y);

      self.anomalyLayer.appendChild(svgElement("circle", {
        cx: x,
        cy: y,
        r: 7.5,
        fill: color,
        stroke: "#fff1ea",
        "stroke-width": 1.5
      }));

      self.anomalyLayer.appendChild(svgElement("circle", {
        cx: x,
        cy: y,
        r: 15,
        fill: "none",
        stroke: color,
        "stroke-width": 1.2,
        "stroke-dasharray": "3 4",
        opacity: "0.8"
      }));
    });
  };

  global.PatrolFloorMap = {
    create: create
  };
  global.window.PatrolFloorMap = global.PatrolFloorMap;
})(window);
