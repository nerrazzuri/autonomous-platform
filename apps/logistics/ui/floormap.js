(function (global) {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";

  const DEFAULTS = {
    width: 500,
    height: 300,
    minX: -10,
    maxX: 10,
    minY: -10,
    maxY: 10,
    title: "Factory Floor Map",
    stations: []
  };

  function create(container, options) {
    return new FloorMapInstance(resolveContainer(container), options || {});
  }

  function resolveContainer(container) {
    if (typeof container === "string") {
      const resolved = document.querySelector(container);
      if (!resolved) {
        throw new Error("FloorMap container not found: " + container);
      }
      return resolved;
    }

    if (container && container.nodeType === 1) {
      return container;
    }

    throw new Error("FloorMap container must be a selector or DOM element.");
  }

  function createSvgElement(tagName, attributes) {
    const element = document.createElementNS(SVG_NS, tagName);
    Object.keys(attributes || {}).forEach(function (key) {
      element.setAttribute(key, String(attributes[key]));
    });
    return element;
  }

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function normalizePosition(position) {
    if (Array.isArray(position) && position.length >= 2) {
      const x = Number(position[0]);
      const y = Number(position[1]);
      const z = position.length > 2 ? Number(position[2]) : 0;
      if (Number.isFinite(x) && Number.isFinite(y)) {
        return { x: x, y: y, z: Number.isFinite(z) ? z : 0 };
      }
      return null;
    }

    if (position && typeof position === "object") {
      const x = Number(position.x);
      const y = Number(position.y);
      const z = "z" in position ? Number(position.z) : 0;
      if (Number.isFinite(x) && Number.isFinite(y)) {
        return { x: x, y: y, z: Number.isFinite(z) ? z : 0 };
      }
    }

    return null;
  }

  function normalizeStation(station) {
    if (Array.isArray(station) && station.length >= 2) {
      const x = Number(station[0]);
      const y = Number(station[1]);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        return {
          x: x,
          y: y,
          label: station[2] != null ? String(station[2]) : "Station"
        };
      }
      return null;
    }

    if (station && typeof station === "object") {
      const x = Number(station.x);
      const y = Number(station.y);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        return {
          x: x,
          y: y,
          label: String(station.label || station.name || station.id || "Station")
        };
      }
    }

    return null;
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function formatCoord(value) {
    return Number(value).toFixed(2);
  }

  function FloorMapInstance(container, options) {
    this.container = container;
    this.options = Object.assign({}, DEFAULTS, options || {});
    this.innerPadding = {
      top: 38,
      right: 26,
      bottom: 40,
      left: 34
    };
    this.root = null;
    this.svg = null;
    this.mapSurface = null;
    this.dot = null;
    this.dotGlow = null;
    this.statusLabel = null;
    this.destroyed = false;

    if (!(this.options.maxX > this.options.minX)) {
      this.options.maxX = DEFAULTS.maxX;
      this.options.minX = DEFAULTS.minX;
    }

    if (!(this.options.maxY > this.options.minY)) {
      this.options.maxY = DEFAULTS.maxY;
      this.options.minY = DEFAULTS.minY;
    }

    this._render();
    this.clear();
  }

  FloorMapInstance.prototype._render = function () {
    const width = this.options.width;
    const height = this.options.height;
    const plotWidth = width - this.innerPadding.left - this.innerPadding.right;
    const plotHeight = height - this.innerPadding.top - this.innerPadding.bottom;

    this.container.innerHTML = "";

    this.root = document.createElement("div");
    this.root.style.width = "100%";
    this.root.style.minHeight = String(height) + "px";

    this.svg = createSvgElement("svg", {
      viewBox: "0 0 " + width + " " + height,
      width: "100%",
      height: "100%",
      role: "img",
      "aria-label": this.options.title
    });
    this.svg.style.display = "block";

    const defs = createSvgElement("defs");
    const glowFilter = createSvgElement("filter", {
      id: "floor-map-dot-glow",
      x: "-50%",
      y: "-50%",
      width: "200%",
      height: "200%"
    });
    glowFilter.appendChild(createSvgElement("feGaussianBlur", {
      stdDeviation: "4",
      result: "blur"
    }));
    defs.appendChild(glowFilter);
    this.svg.appendChild(defs);

    this.svg.appendChild(createSvgElement("rect", {
      x: 0,
      y: 0,
      width: width,
      height: height,
      rx: 18,
      fill: "#0f1724"
    }));

    const title = createSvgElement("text", {
      x: this.innerPadding.left,
      y: 24,
      fill: "#e8edf5",
      "font-size": 16,
      "font-weight": 700,
      "letter-spacing": 0.4
    });
    title.textContent = this.options.title;
    this.svg.appendChild(title);

    this.mapSurface = createSvgElement("g", {
      transform: "translate(" + this.innerPadding.left + " " + this.innerPadding.top + ")"
    });

    this.mapSurface.appendChild(createSvgElement("rect", {
      x: 0,
      y: 0,
      width: plotWidth,
      height: plotHeight,
      rx: 14,
      fill: "#152131",
      stroke: "#3c5064",
      "stroke-width": 1.25
    }));

    for (let index = 1; index < 5; index += 1) {
      const x = (plotWidth / 5) * index;
      this.mapSurface.appendChild(createSvgElement("line", {
        x1: x,
        y1: 0,
        x2: x,
        y2: plotHeight,
        stroke: "rgba(198, 213, 230, 0.12)",
        "stroke-width": 1
      }));
    }

    for (let index = 1; index < 4; index += 1) {
      const y = (plotHeight / 4) * index;
      this.mapSurface.appendChild(createSvgElement("line", {
        x1: 0,
        y1: y,
        x2: plotWidth,
        y2: y,
        stroke: "rgba(198, 213, 230, 0.12)",
        "stroke-width": 1
      }));
    }

    const horizontalAxis = this._mapY(0);
    if (isFiniteNumber(horizontalAxis)) {
      this.mapSurface.appendChild(createSvgElement("line", {
        x1: 0,
        y1: horizontalAxis,
        x2: plotWidth,
        y2: horizontalAxis,
        stroke: "rgba(107, 165, 232, 0.32)",
        "stroke-width": 1
      }));
    }

    const verticalAxis = this._mapX(0);
    if (isFiniteNumber(verticalAxis)) {
      this.mapSurface.appendChild(createSvgElement("line", {
        x1: verticalAxis,
        y1: 0,
        x2: verticalAxis,
        y2: plotHeight,
        stroke: "rgba(107, 165, 232, 0.32)",
        "stroke-width": 1
      }));
    }

    this._renderStations();

    this.dotGlow = createSvgElement("circle", {
      cx: plotWidth / 2,
      cy: plotHeight / 2,
      r: 10,
      fill: "rgba(255, 193, 71, 0.5)",
      filter: "url(#floor-map-dot-glow)"
    });

    this.dot = createSvgElement("circle", {
      cx: plotWidth / 2,
      cy: plotHeight / 2,
      r: 5.5,
      fill: "#ffc35b",
      stroke: "#fff3d0",
      "stroke-width": 1.5
    });

    this.mapSurface.appendChild(this.dotGlow);
    this.mapSurface.appendChild(this.dot);
    this.svg.appendChild(this.mapSurface);

    this.statusLabel = createSvgElement("text", {
      x: this.innerPadding.left,
      y: height - 14,
      fill: "#c2cedd",
      "font-size": 13,
      "font-weight": 600
    });
    this.svg.appendChild(this.statusLabel);

    this.root.appendChild(this.svg);
    this.container.appendChild(this.root);
  };

  FloorMapInstance.prototype._renderStations = function () {
    const stations = Array.isArray(this.options.stations) ? this.options.stations : [];
    const plotWidth = this.options.width - this.innerPadding.left - this.innerPadding.right;
    const plotHeight = this.options.height - this.innerPadding.top - this.innerPadding.bottom;

    stations.forEach(function (station) {
      const normalized = normalizeStation(station);
      if (!normalized) {
        return;
      }

      const x = this._mapX(normalized.x);
      const y = this._mapY(normalized.y);
      if (!isFiniteNumber(x) || !isFiniteNumber(y)) {
        return;
      }

      const stationGroup = createSvgElement("g");
      stationGroup.appendChild(createSvgElement("circle", {
        cx: x,
        cy: y,
        r: 4,
        fill: "#71b3f3"
      }));

      const label = createSvgElement("text", {
        x: clamp(x + 8, 8, plotWidth - 8),
        y: clamp(y - 8, 12, plotHeight - 8),
        fill: "#dbe7f2",
        "font-size": 11,
        "font-weight": 700
      });
      label.textContent = normalized.label;
      stationGroup.appendChild(label);
      this.mapSurface.appendChild(stationGroup);
    }, this);
  };

  FloorMapInstance.prototype._mapX = function (x) {
    const plotWidth = this.options.width - this.innerPadding.left - this.innerPadding.right;
    const clamped = clamp(Number(x), this.options.minX, this.options.maxX);
    const ratio = (clamped - this.options.minX) / (this.options.maxX - this.options.minX);
    return ratio * plotWidth;
  };

  FloorMapInstance.prototype._mapY = function (y) {
    const plotHeight = this.options.height - this.innerPadding.top - this.innerPadding.bottom;
    const clamped = clamp(Number(y), this.options.minY, this.options.maxY);
    const ratio = (clamped - this.options.minY) / (this.options.maxY - this.options.minY);
    return plotHeight - (ratio * plotHeight);
  };

  FloorMapInstance.prototype.updatePosition = function (position) {
    if (this.destroyed) {
      return this;
    }

    const normalized = normalizePosition(position);
    if (!normalized) {
      return this;
    }

    const x = this._mapX(normalized.x);
    const y = this._mapY(normalized.y);
    if (!isFiniteNumber(x) || !isFiniteNumber(y)) {
      return this;
    }

    this.dot.setAttribute("cx", String(x));
    this.dot.setAttribute("cy", String(y));
    this.dot.setAttribute("visibility", "visible");
    this.dotGlow.setAttribute("cx", String(x));
    this.dotGlow.setAttribute("cy", String(y));
    this.dotGlow.setAttribute("visibility", "visible");
    this.updateStatus("X: " + formatCoord(normalized.x) + " | Y: " + formatCoord(normalized.y));
    return this;
  };

  FloorMapInstance.prototype.updateStatus = function (statusText) {
    if (this.destroyed || !this.statusLabel) {
      return this;
    }

    this.statusLabel.textContent = String(statusText == null || statusText === "" ? "No position" : statusText);
    return this;
  };

  FloorMapInstance.prototype.clear = function () {
    if (this.destroyed) {
      return this;
    }

    this.dot.setAttribute("visibility", "hidden");
    this.dotGlow.setAttribute("visibility", "hidden");
    this.updateStatus("No position");
    return this;
  };

  FloorMapInstance.prototype.destroy = function () {
    if (this.destroyed) {
      return;
    }

    this.destroyed = true;
    if (this.root && this.root.parentNode === this.container) {
      this.container.removeChild(this.root);
    } else {
      this.container.innerHTML = "";
    }

    this.root = null;
    this.svg = null;
    this.mapSurface = null;
    this.dot = null;
    this.dotGlow = null;
    this.statusLabel = null;
  };

  global.FloorMap = {
    create: create
  };
  global.window.FloorMap = global.FloorMap;
})(window);
