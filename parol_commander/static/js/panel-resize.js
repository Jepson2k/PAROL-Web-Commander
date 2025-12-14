/**
 * Panel Resize Module
 *
 * Generic resizable panel system. Configure via PanelResize.configure()
 * from Python with app-specific selectors and constraints.
 *
 * Supports multiple resizable panels with per-panel size memory.
 * Only one top panel and one bottom panel can be visible at a time,
 * but all panels remember their individual dimensions.
 */

(function() {
    'use strict';

    // ========== Configuration ==========
    // All app-specific values come from configure() call
    let config = {
        storageKey: 'panel_sizes',
        selectors: {
            wrap: null,
            topContainer: null,
            bottomContainer: null
        },
        constraints: {
            viewportMarginX: 80,
            viewportMarginY: 100,
            containerPadding: 20,
            bottomOffset: 12,
            totalMargin: 36
        },
        stateClasses: {
            bottomOpen: 'bottom-open',
            bottomOpenNonProgram: 'bottom-open-non-program',
            panelOpen: 'is-open'
        },
        panels: {}
    };

    let configured = false;
    let appReady = false;

    // ========== Per-Panel Size Storage ==========
    let panelSizes = {};

    function loadPanelSizes() {
        try {
            const saved = localStorage.getItem(config.storageKey);
            if (saved) {
                panelSizes = JSON.parse(saved);
                console.log('[PanelResize] Loaded panel sizes:', panelSizes);
                // Set CSS variables immediately so they're available before panels exist
                for (const [panelId, sizes] of Object.entries(panelSizes)) {
                    if (sizes.height) {
                        document.documentElement.style.setProperty(`--panel-height-${panelId}`, sizes.height + 'px');
                    }
                    if (sizes.width) {
                        document.documentElement.style.setProperty(`--panel-width-${panelId}`, sizes.width + 'px');
                    }
                }
            }
        } catch (e) {
            console.warn('[PanelResize] Could not load panel sizes:', e);
            panelSizes = {};
        }
    }

    function savePanelSizes() {
        try {
            localStorage.setItem(config.storageKey, JSON.stringify(panelSizes));
        } catch (e) {
            console.warn('[PanelResize] Could not save panel sizes:', e);
        }
    }

    // ========== Panel Identification ==========

    function getPanelId(panel) {
        if (panel.dataset && panel.dataset.panelId) {
            return panel.dataset.panelId;
        }
        for (const [panelId, panelConfig] of Object.entries(config.panels)) {
            if (panelConfig.selector && panel.matches(panelConfig.selector)) {
                return panelId;
            }
        }
        for (const className of panel.classList) {
            if (className.endsWith('-panel') && className !== 'resizable-tab') {
                return className.replace('-panel', '');
            }
        }
        return null;
    }

    function getPanelGroup(panel) {
        const panelId = getPanelId(panel);
        if (panelId && config.panels[panelId]) {
            return config.panels[panelId].group;
        }
        if (config.selectors.bottomContainer) {
            const bottomContainer = document.querySelector(config.selectors.bottomContainer);
            if (bottomContainer && bottomContainer.contains(panel)) {
                return 'bottom';
            }
        }
        if (config.selectors.topContainer) {
            const topContainer = document.querySelector(config.selectors.topContainer);
            if (topContainer && topContainer.contains(panel)) {
                return 'top';
            }
        }
        return 'unknown';
    }

    function getPanelById(panelId) {
        const panelConfig = config.panels[panelId];
        if (panelConfig && panelConfig.selector) {
            return document.querySelector(panelConfig.selector);
        }
        return document.querySelector(`.${panelId}-panel`);
    }

    // ========== Size Management ==========

    function savePanelSize(panel, width, height) {
        const panelId = getPanelId(panel);
        if (!panelId) return;

        if (!panelSizes[panelId]) {
            panelSizes[panelId] = {};
        }
        if (width !== undefined && width !== null) {
            panelSizes[panelId].width = width;
        }
        if (height !== undefined && height !== null) {
            panelSizes[panelId].height = height;
            document.documentElement.style.setProperty(`--panel-height-${panelId}`, height + 'px');
        }
        panelSizes[panelId].group = getPanelGroup(panel);

        console.log('[PanelResize] Saved size for', panelId + ':', panelSizes[panelId]);
        savePanelSizes();
    }

    function getSavedPanelSize(panelId) {
        return panelSizes[panelId] || null;
    }

    function restorePanelSize(panel) {
        const panelId = getPanelId(panel);
        if (!panelId) return false;

        const saved = panelSizes[panelId];
        if (!saved) return false;

        const panelConfig = getPanelConfig(panel);
        const maxW = getMaxWidth();
        const maxH = getMaxHeight();

        console.log('[PanelResize] Restoring size for', panelId + ':', saved);

        if (saved.width && saved.width >= panelConfig.minWidth && saved.width <= maxW) {
            panel.style.setProperty('width', saved.width + 'px', 'important');
            panel.style.setProperty('flex-basis', saved.width + 'px', 'important');
            panel.style.setProperty('flex-grow', '0', 'important');
            panel.style.setProperty('flex-shrink', '0', 'important');
            panel.style.setProperty('max-width', saved.width + 'px', 'important');
            panel.style.setProperty('min-width', saved.width + 'px', 'important');

            // Only update container widths for explicitly configured panels (program, response)
            const isConfiguredPanel = panelId && config.panels[panelId];

            if (isConfiguredPanel && config.selectors.topContainer && panelConfig.group === 'top') {
                const topContainer = panel.closest(config.selectors.topContainer);
                if (topContainer) {
                    topContainer.style.setProperty('max-width', (saved.width + config.constraints.containerPadding) + 'px', 'important');
                    topContainer.style.setProperty('width', (saved.width + config.constraints.containerPadding) + 'px', 'important');
                }
            }

            // Also restore bottom container width for bottom panels
            if (isConfiguredPanel && config.selectors.bottomContainer && panelConfig.group === 'bottom') {
                const bottomContainer = panel.closest(config.selectors.bottomContainer);
                if (bottomContainer) {
                    bottomContainer.style.setProperty('width', (saved.width + config.constraints.containerPadding) + 'px', 'important');
                }
            }
        }

        if (saved.height && saved.height >= panelConfig.minHeight && saved.height <= maxH) {
            panel.style.setProperty('max-height', saved.height + 'px', 'important');
            document.documentElement.style.setProperty(`--panel-height-${panelId}`, saved.height + 'px');

            if (getPanelGroup(panel) === 'bottom') {
                panel.style.setProperty('height', saved.height + 'px', 'important');
                if (config.selectors.bottomContainer) {
                    const bottomContainer = panel.closest(config.selectors.bottomContainer);
                    if (bottomContainer) {
                        bottomContainer.style.setProperty('height', saved.height + 'px', 'important');
                    }
                }
            }
        }

        return true;
    }

    // ========== Configuration Helpers ==========

    function getPanelConfig(panel) {
        const panelId = getPanelId(panel);

        if (panelId && config.panels[panelId]) {
            const cfg = config.panels[panelId];
            return {
                minWidth: cfg.minWidth,
                minHeight: cfg.minHeight,
                maxWidth: getMaxWidth(),
                maxHeight: getMaxHeight(),
                group: cfg.group,
                pushTarget: cfg.pushTarget
            };
        }

        return {
            minWidth: 200,
            minHeight: 100,
            maxWidth: getMaxWidth(),
            maxHeight: getMaxHeight(),
            group: 'unknown',
            pushTarget: null
        };
    }

    function getMaxWidth() {
        return window.innerWidth - config.constraints.viewportMarginX;
    }

    function getMaxHeight() {
        return window.innerHeight - config.constraints.viewportMarginY;
    }

    // ========== Resize State ==========
    let isResizing = false;
    let resizeType = null;
    let startX = 0;
    let startY = 0;
    let startWidth = 0;
    let startHeight = 0;
    let activePanel = null;
    let activeHandle = null;
    let invertHeight = false;
    let startPanelTop = 0;
    let startPanelBottom = 0;
    let lastPushedPanel = null;

    // ========== Resize Logic ==========

    function onMouseMove(e) {
        if (!isResizing || !activePanel) return;

        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        const clientY = e.touches ? e.touches[0].clientY : e.clientY;
        const panelConfig = getPanelConfig(activePanel);

        // Handle width resize
        if (resizeType === 'width' || resizeType === 'both') {
            const deltaX = clientX - startX;
            let newWidth = startWidth + deltaX;
            newWidth = Math.max(panelConfig.minWidth, Math.min(newWidth, panelConfig.maxWidth));

            activePanel.style.setProperty('width', newWidth + 'px', 'important');
            activePanel.style.setProperty('flex-basis', newWidth + 'px', 'important');
            activePanel.style.setProperty('flex-grow', '0', 'important');
            activePanel.style.setProperty('flex-shrink', '0', 'important');
            activePanel.style.setProperty('max-width', newWidth + 'px', 'important');
            activePanel.style.setProperty('min-width', newWidth + 'px', 'important');

            // Only update container widths for explicitly configured panels (program, response)
            // IO and Gripper tabs should keep natural sizing
            const panelId = getPanelId(activePanel);
            const isConfiguredPanel = panelId && config.panels[panelId];

            if (isConfiguredPanel && config.selectors.topContainer && panelConfig.group === 'top') {
                const topContainer = activePanel.closest(config.selectors.topContainer);
                if (topContainer) {
                    topContainer.style.setProperty('max-width', (newWidth + config.constraints.containerPadding) + 'px', 'important');
                    topContainer.style.setProperty('width', (newWidth + config.constraints.containerPadding) + 'px', 'important');
                }
            }

            // Also update bottom container width for bottom panels
            if (isConfiguredPanel && config.selectors.bottomContainer && panelConfig.group === 'bottom') {
                const bottomContainer = activePanel.closest(config.selectors.bottomContainer);
                if (bottomContainer) {
                    bottomContainer.style.setProperty('width', (newWidth + config.constraints.containerPadding) + 'px', 'important');
                }
            }
        }

        // Handle height resize with push logic
        if (resizeType === 'height' || resizeType === 'both') {
            let deltaY = clientY - startY;
            if (invertHeight) deltaY = -deltaY;

            let newHeight = startHeight + deltaY;

            const wrap = config.selectors.wrap ? document.querySelector(config.selectors.wrap) : null;
            const bottomContainer = config.selectors.bottomContainer ? document.querySelector(config.selectors.bottomContainer) : null;
            const isBottomOpen = wrap && wrap.classList.contains(config.stateClasses.bottomOpen);
            const isProgramActive = wrap && !wrap.classList.contains(config.stateClasses.bottomOpenNonProgram);

            const viewportHeight = window.innerHeight;
            const availableHeight = viewportHeight - config.constraints.totalMargin;

            const pushTargetId = panelConfig.pushTarget;
            const pushTargetPanel = pushTargetId ? getPanelById(pushTargetId) : null;
            const pushTargetConfig = pushTargetPanel ? getPanelConfig(pushTargetPanel) : null;

            // Top panel resizing (push bottom)
            if (panelConfig.group === 'top' && isBottomOpen && pushTargetPanel && bottomContainer) {
                const bottomHeight = bottomContainer.offsetHeight;
                const bottomMinHeight = pushTargetConfig ? pushTargetConfig.minHeight : 100;

                newHeight = Math.max(panelConfig.minHeight, Math.min(newHeight, availableHeight));

                if ((newHeight + bottomHeight) > availableHeight) {
                    const newBottomHeight = availableHeight - newHeight;
                    if (newBottomHeight < bottomMinHeight) {
                        newHeight = availableHeight - bottomMinHeight;
                        bottomContainer.style.setProperty('height', bottomMinHeight + 'px', 'important');
                        pushTargetPanel.style.setProperty('height', bottomMinHeight + 'px', 'important');
                    } else {
                        bottomContainer.style.setProperty('height', newBottomHeight + 'px', 'important');
                        pushTargetPanel.style.setProperty('height', newBottomHeight + 'px', 'important');
                    }
                    lastPushedPanel = pushTargetPanel;
                }

                if (wrap) {
                    wrap.style.setProperty('height', newHeight + 'px', 'important');
                }
            }
            // Bottom panel with top handle (top-based positioning)
            else if (panelConfig.group === 'bottom' && bottomContainer && invertHeight && startPanelBottom > 0) {
                const newTop = startPanelTop + (clientY - startY);
                newHeight = startPanelBottom - newTop;

                newHeight = Math.max(panelConfig.minHeight, Math.min(newHeight, availableHeight));

                const newBottomFromViewport = viewportHeight - startPanelBottom;

                // Only push top panel if program tab is active
                if (pushTargetPanel && isProgramActive) {
                    const topHeight = pushTargetPanel.offsetHeight;
                    const topMinHeight = pushTargetConfig ? pushTargetConfig.minHeight : 300;

                    if ((newHeight + topHeight) > availableHeight) {
                        const newTopHeight = availableHeight - newHeight;
                        if (newTopHeight < topMinHeight) {
                            newHeight = availableHeight - topMinHeight;
                            pushTargetPanel.style.setProperty('max-height', topMinHeight + 'px', 'important');
                        } else {
                            pushTargetPanel.style.setProperty('max-height', newTopHeight + 'px', 'important');
                        }
                        lastPushedPanel = pushTargetPanel;
                    }

                    if (wrap) {
                        wrap.style.setProperty('height', 'calc(100% - ' + newHeight + 'px - 24px)', 'important');
                    }
                }

                bottomContainer.style.setProperty('bottom', newBottomFromViewport + 'px', 'important');
                bottomContainer.style.setProperty('height', newHeight + 'px', 'important');
            }
            // Bottom panel with other handles
            else if (panelConfig.group === 'bottom' && pushTargetPanel) {
                const topHeight = pushTargetPanel.offsetHeight;
                const topMinHeight = pushTargetConfig ? pushTargetConfig.minHeight : 300;

                newHeight = Math.max(panelConfig.minHeight, Math.min(newHeight, availableHeight));

                if ((newHeight + topHeight) > availableHeight) {
                    const newTopHeight = availableHeight - newHeight;
                    if (newTopHeight < topMinHeight) {
                        newHeight = availableHeight - topMinHeight;
                        pushTargetPanel.style.setProperty('max-height', topMinHeight + 'px', 'important');
                    } else {
                        pushTargetPanel.style.setProperty('max-height', newTopHeight + 'px', 'important');
                    }
                    lastPushedPanel = pushTargetPanel;
                }

                if (wrap) {
                    wrap.style.setProperty('height', 'calc(100% - ' + newHeight + 'px - 24px)', 'important');
                }
                if (bottomContainer) {
                    bottomContainer.style.setProperty('height', newHeight + 'px', 'important');
                }
            } else {
                newHeight = Math.max(panelConfig.minHeight, Math.min(newHeight, panelConfig.maxHeight));
            }

            activePanel.style.setProperty('max-height', newHeight + 'px', 'important');

            if (panelConfig.group === 'bottom') {
                activePanel.style.setProperty('height', newHeight + 'px', 'important');
            }
        }
    }

    function onMouseUp() {
        if (!isResizing) return;

        // Save active panel size
        if (activePanel) {
            savePanelSize(activePanel, activePanel.offsetWidth, activePanel.offsetHeight);
        }

        // Save pushed panel size (the panel that was pushed during resize)
        if (lastPushedPanel) {
            savePanelSize(lastPushedPanel, null, lastPushedPanel.offsetHeight);
        }

        isResizing = false;
        resizeType = null;
        invertHeight = false;
        lastPushedPanel = null;
        if (activeHandle) activeHandle.classList.remove('dragging');
        document.body.classList.remove('resizing-panel');
        document.body.style.cursor = '';
        activePanel = null;
        activeHandle = null;
    }

    // ========== Handle Attachment ==========

    function attachRightHandle(handle, panel) {
        if (handle._resizeAttached) return;
        handle._resizeAttached = true;

        function start(e) {
            e.preventDefault();
            e.stopPropagation();
            isResizing = true;
            resizeType = 'width';
            startX = e.touches ? e.touches[0].clientX : e.clientX;
            startWidth = panel.offsetWidth;
            activePanel = panel;
            activeHandle = handle;
            lastPushedPanel = null;
            handle.classList.add('dragging');
            document.body.classList.add('resizing-panel');
            document.body.style.cursor = 'ew-resize';
        }

        handle.addEventListener('mousedown', start);
        handle.addEventListener('touchstart', start, { passive: false });
    }

    function attachBottomHandle(handle, panel) {
        if (handle._resizeAttached) return;
        handle._resizeAttached = true;

        function start(e) {
            e.preventDefault();
            e.stopPropagation();
            isResizing = true;
            resizeType = 'height';
            invertHeight = false;
            startY = e.touches ? e.touches[0].clientY : e.clientY;
            startHeight = panel.offsetHeight;
            activePanel = panel;
            activeHandle = handle;
            lastPushedPanel = null;
            handle.classList.add('dragging');
            document.body.classList.add('resizing-panel');
            document.body.style.cursor = 'ns-resize';
        }

        handle.addEventListener('mousedown', start);
        handle.addEventListener('touchstart', start, { passive: false });
    }

    function attachTopHandle(handle, panel) {
        if (handle._resizeAttached) return;
        handle._resizeAttached = true;

        function start(e) {
            e.preventDefault();
            e.stopPropagation();
            isResizing = true;
            resizeType = 'height';
            invertHeight = true;
            startY = e.touches ? e.touches[0].clientY : e.clientY;
            lastPushedPanel = null;

            const panelConfig = getPanelConfig(panel);
            if (panelConfig.group === 'bottom' && config.selectors.bottomContainer) {
                const bottomContainer = document.querySelector(config.selectors.bottomContainer);
                if (bottomContainer) {
                    const rect = bottomContainer.getBoundingClientRect();
                    startPanelTop = rect.top;
                    startPanelBottom = rect.bottom;
                    startHeight = rect.height;
                } else {
                    startHeight = panel.offsetHeight;
                    startPanelTop = 0;
                    startPanelBottom = 0;
                }
            } else {
                startHeight = panel.offsetHeight;
                startPanelTop = 0;
                startPanelBottom = 0;
            }

            activePanel = panel;
            activeHandle = handle;
            handle.classList.add('dragging');
            document.body.classList.add('resizing-panel');
            document.body.style.cursor = 'ns-resize';
        }

        handle.addEventListener('mousedown', start);
        handle.addEventListener('touchstart', start, { passive: false });
    }

    function attachCornerHandle(handle, panel, isTopCorner) {
        if (handle._resizeAttached) return;
        handle._resizeAttached = true;

        function start(e) {
            e.preventDefault();
            e.stopPropagation();
            isResizing = true;
            resizeType = 'both';
            invertHeight = isTopCorner;
            startX = e.touches ? e.touches[0].clientX : e.clientX;
            startY = e.touches ? e.touches[0].clientY : e.clientY;
            startWidth = panel.offsetWidth;
            lastPushedPanel = null;

            const panelConfig = getPanelConfig(panel);
            if (isTopCorner && panelConfig.group === 'bottom' && config.selectors.bottomContainer) {
                const bottomContainer = document.querySelector(config.selectors.bottomContainer);
                if (bottomContainer) {
                    const rect = bottomContainer.getBoundingClientRect();
                    startPanelTop = rect.top;
                    startPanelBottom = rect.bottom;
                    startHeight = rect.height;
                } else {
                    startHeight = panel.offsetHeight;
                    startPanelTop = 0;
                    startPanelBottom = 0;
                }
            } else {
                startHeight = panel.offsetHeight;
                startPanelTop = 0;
                startPanelBottom = 0;
            }

            activePanel = panel;
            activeHandle = handle;
            handle.classList.add('dragging');
            document.body.classList.add('resizing-panel');
            document.body.style.cursor = isTopCorner ? 'nesw-resize' : 'nwse-resize';
        }

        handle.addEventListener('mousedown', start);
        handle.addEventListener('touchstart', start, { passive: false });
    }

    // ========== Panel Initialization ==========

    function initPanel(panel) {
        if (panel._panelResizeInit) return;
        panel._panelResizeInit = true;

        const rightHandle = panel.querySelector('.resize-handle-right');
        const bottomHandle = panel.querySelector('.resize-handle-bottom');
        const topHandle = panel.querySelector('.resize-handle-top');
        const cornerHandle = panel.querySelector('.resize-handle-corner');

        if (rightHandle) attachRightHandle(rightHandle, panel);
        if (bottomHandle) attachBottomHandle(bottomHandle, panel);
        if (topHandle) attachTopHandle(topHandle, panel);

        if (cornerHandle) {
            const isTopCorner = getPanelGroup(panel) === 'bottom';
            attachCornerHandle(cornerHandle, panel, isTopCorner);
        }

        console.log('[PanelResize] Panel initialized:', getPanelId(panel));
    }

    function initAllPanels() {
        const selectors = [];
        for (const panelConfig of Object.values(config.panels)) {
            if (panelConfig.selector) {
                selectors.push(panelConfig.selector);
            }
        }
        selectors.push('.resizable-tab');

        const resizablePanels = document.querySelectorAll(selectors.join(', '));

        const panelSet = new Set();
        resizablePanels.forEach(panel => {
            if (config.selectors.topContainer && panel.matches(config.selectors.topContainer)) return;
            if (config.selectors.bottomContainer && panel.matches(config.selectors.bottomContainer)) return;
            panelSet.add(panel);
        });

        panelSet.forEach(initPanel);
        console.log('[PanelResize] Initialized', panelSet.size, 'panels');
    }

    // ========== Tab Change Handling ==========

    function onTabChange(group, toTab) {
        console.log('[PanelResize] Tab change:', group, '->', toTab);

        const wrap = config.selectors.wrap ? document.querySelector(config.selectors.wrap) : null;
        const bottomContainer = config.selectors.bottomContainer ? document.querySelector(config.selectors.bottomContainer) : null;
        const topContainer = config.selectors.topContainer ? document.querySelector(config.selectors.topContainer) : null;

        if (group === 'top') {
            const isProgramTab = toTab === 'program';
            const isClosing = !toTab;

            if (!isProgramTab) {
                if (topContainer) {
                    topContainer.style.removeProperty('width');
                    topContainer.style.removeProperty('max-width');
                }

                const programPanel = getPanelById('program');
                if (programPanel) {
                    programPanel.style.removeProperty('width');
                    programPanel.style.removeProperty('max-width');
                    programPanel.style.removeProperty('min-width');
                    programPanel.style.removeProperty('flex-basis');
                }

                if (wrap) {
                    wrap.style.removeProperty('height');
                    wrap.classList.remove(config.stateClasses.bottomOpen);

                    if (isClosing) {
                        wrap.classList.remove(config.stateClasses.bottomOpenNonProgram);
                    } else {
                        wrap.classList.add(config.stateClasses.bottomOpenNonProgram);
                    }
                }

                const hasIsOpenClass = bottomContainer && bottomContainer.classList.contains(config.stateClasses.panelOpen);

                if (hasIsOpenClass && !isClosing) {
                    const currentHeight = bottomContainer.offsetHeight;
                    bottomContainer.style.setProperty('bottom', config.constraints.bottomOffset + 'px', 'important');
                    bottomContainer.style.setProperty('height', currentHeight + 'px', 'important');
                }
            } else {
                if (wrap) {
                    wrap.classList.remove(config.stateClasses.bottomOpenNonProgram);

                    const isBottomOpen = bottomContainer && (
                        bottomContainer.classList.contains(config.stateClasses.panelOpen) ||
                        bottomContainer.offsetHeight > 0
                    );
                    if (isBottomOpen) {
                        wrap.classList.add(config.stateClasses.bottomOpen);

                        const bottomHeight = bottomContainer.offsetHeight;
                        bottomContainer.style.setProperty('bottom', config.constraints.bottomOffset + 'px', 'important');
                        bottomContainer.style.setProperty('height', bottomHeight + 'px', 'important');
                        wrap.style.setProperty('height', 'calc(100% - ' + (bottomHeight + config.constraints.bottomOffset) + 'px)', 'important');
                    }
                }

                const programPanel = getPanelById('program');
                if (programPanel) {
                    restorePanelSize(programPanel);
                }
            }
        } else if (group === 'bottom') {
            const isOpening = !!toTab;
            const isClosing = !toTab;

            const isProgramActive = wrap && !wrap.classList.contains(config.stateClasses.bottomOpenNonProgram);

            if (isOpening) {
                if (bottomContainer) {
                    bottomContainer.classList.add(config.stateClasses.panelOpen);
                }

                if (wrap) {
                    if (isProgramActive) {
                        wrap.classList.add(config.stateClasses.bottomOpen);
                        wrap.classList.remove(config.stateClasses.bottomOpenNonProgram);
                    } else {
                        wrap.classList.add(config.stateClasses.bottomOpenNonProgram);
                        wrap.classList.remove(config.stateClasses.bottomOpen);
                    }
                }

                const bottomPanel = getPanelById('response');
                if (bottomPanel) {
                    restorePanelSize(bottomPanel);
                }
            } else if (isClosing) {
                const bottomPanel = getPanelById('response');
                if (bottomPanel && bottomContainer) {
                    const currentHeight = bottomContainer.offsetHeight;
                    if (currentHeight > 0) {
                        savePanelSize(bottomPanel, null, currentHeight);
                    }
                }

                if (bottomContainer) {
                    bottomContainer.classList.remove(config.stateClasses.panelOpen);
                    bottomContainer.style.removeProperty('height');
                    bottomContainer.style.removeProperty('bottom');
                }

                if (wrap) {
                    wrap.classList.remove(config.stateClasses.bottomOpen);
                    wrap.classList.remove(config.stateClasses.bottomOpenNonProgram);
                    wrap.style.removeProperty('height');
                }
            }
        }
    }

    // ========== App Ready Signal ==========

    function onAppReady() {
        console.log('[PanelResize] App ready signal received');
        appReady = true;

        initAllPanels();

        // Delay restoration to ensure panels are fully rendered
        setTimeout(function() {
            restoreAllSizes();
            // Verify and retry if needed
            setTimeout(verifyRestoration, 300);
        }, 100);
    }

    function restoreAllSizes() {
        for (const panelId of Object.keys(panelSizes)) {
            const panel = getPanelById(panelId);
            if (panel) {
                restorePanelSize(panel);
            }
        }
    }

    function verifyRestoration() {
        for (const [panelId, saved] of Object.entries(panelSizes)) {
            const panel = getPanelById(panelId);
            if (!panel) continue;

            const current = {
                width: panel.offsetWidth,
                height: panel.offsetHeight
            };

            const widthMismatch = saved.width && Math.abs(saved.width - current.width) > 5;
            const heightMismatch = saved.height && Math.abs(saved.height - current.height) > 5;

            if (widthMismatch || heightMismatch) {
                console.log('[PanelResize] Restoration mismatch for', panelId,
                    'saved:', saved, 'current:', current, '- retrying');
                restorePanelSize(panel);
            }
        }
    }

    // ========== Viewport Resize Handler ==========

    function onViewportResize() {
        const maxW = getMaxWidth();

        for (const panelConfig of Object.values(config.panels)) {
            if (!panelConfig.selector) continue;
            const panel = document.querySelector(panelConfig.selector);
            if (!panel) continue;

            const currentWidth = panel.offsetWidth;
            if (currentWidth > maxW) {
                panel.style.setProperty('width', maxW + 'px', 'important');
                panel.style.setProperty('flex-basis', maxW + 'px', 'important');
                panel.style.setProperty('max-width', maxW + 'px', 'important');
                panel.style.setProperty('min-width', Math.min(panelConfig.minWidth, maxW) + 'px', 'important');

                // Only constrain container widths for configured panels
                if (config.selectors.topContainer && panelConfig.group === 'top') {
                    const topContainer = panel.closest(config.selectors.topContainer);
                    if (topContainer) {
                        topContainer.style.setProperty('max-width', (maxW + config.constraints.containerPadding) + 'px', 'important');
                        topContainer.style.setProperty('width', (maxW + config.constraints.containerPadding) + 'px', 'important');
                    }
                }

                // Also constrain bottom container width
                if (config.selectors.bottomContainer && panelConfig.group === 'bottom') {
                    const bottomContainer = panel.closest(config.selectors.bottomContainer);
                    if (bottomContainer) {
                        bottomContainer.style.setProperty('width', (maxW + config.constraints.containerPadding) + 'px', 'important');
                    }
                }
            }
        }
    }

    // ========== Global Event Listeners ==========

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('touchmove', onMouseMove, { passive: false });
    document.addEventListener('touchend', onMouseUp);

    let resizeTimeout = null;
    window.addEventListener('resize', function() {
        document.body.classList.add('viewport-resizing');
        if (resizeTimeout) clearTimeout(resizeTimeout);
        requestAnimationFrame(onViewportResize);
        resizeTimeout = setTimeout(function() {
            document.body.classList.remove('viewport-resizing');
        }, 150);
    });

    // ========== Configuration API ==========

    function configure(userConfig) {
        if (!userConfig) return;

        if (userConfig.storageKey) {
            config.storageKey = userConfig.storageKey;
        }
        if (userConfig.selectors) {
            Object.assign(config.selectors, userConfig.selectors);
        }
        if (userConfig.constraints) {
            Object.assign(config.constraints, userConfig.constraints);
        }
        if (userConfig.stateClasses) {
            Object.assign(config.stateClasses, userConfig.stateClasses);
        }
        if (userConfig.panels) {
            for (const [panelId, panelConfig] of Object.entries(userConfig.panels)) {
                config.panels[panelId] = {
                    ...config.panels[panelId],
                    ...panelConfig,
                    selector: panelConfig.selector || `.${panelId}-panel`
                };
            }
        }

        configured = true;
        console.log('[PanelResize] Configured:', config);

        loadPanelSizes();
    }

    // ========== Setup ==========

    function init() {
        loadPanelSizes();

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() {
                setTimeout(initAllPanels, 200);
            });
        } else {
            setTimeout(initAllPanels, 200);
        }

        function setupObserver() {
            if (!document.body) {
                setTimeout(setupObserver, 50);
                return;
            }

            const observer = new MutationObserver(function(mutations) {
                let shouldInit = false;
                mutations.forEach(function(mutation) {
                    if (mutation.addedNodes.length > 0) {
                        mutation.addedNodes.forEach(function(node) {
                            if (node.nodeType === 1) {
                                if (node.classList && node.classList.contains('resizable-tab')) {
                                    shouldInit = true;
                                } else if (node.querySelector && node.querySelector('.resizable-tab')) {
                                    shouldInit = true;
                                }
                            }
                        });
                    }
                });
                if (shouldInit) {
                    setTimeout(function() {
                        initAllPanels();
                        restoreAllSizes();
                    }, 100);
                }
            });

            observer.observe(document.body, { childList: true, subtree: true });
            console.log('[PanelResize] Observer attached');
        }

        setupObserver();
    }

    init();

    // ========== Public API ==========

    window.PanelResize = {
        configure: configure,
        init: initAllPanels,
        initPanel: function(selector) {
            const panel = document.querySelector(selector);
            if (panel) initPanel(panel);
        },
        onTabChange: onTabChange,
        onAppReady: onAppReady,
        restoreAllSizes: restoreAllSizes,
        getSavedSize: getSavedPanelSize,
        clearAllSizes: function() {
            panelSizes = {};
            savePanelSizes();
            console.log('[PanelResize] Sizes cleared');
        },
        getConfig: function() { return config; },
        getSizes: function() { return panelSizes; },
        isConfigured: function() { return configured; },
        isAppReady: function() { return appReady; }
    };

})();
