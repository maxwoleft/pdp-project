/************************************
 * Глобальный массив для хранения объектов услуг, добавленных в корзину
 ************************************/
let cartData = [];
window.cartData = cartData;

// Глобальная переменная для сохранения содержимого экрана подтверждения
window.fullConfirmationHTML = "";

// Данные о забронированных слотах текущего пользователя
let reservedSlotsInfo = [];
let reservationInterval = null;
let reservationEndTime = null;


/************************************
 * Попап недоступності API
 ************************************/
function _adminContact() {
  const c = window.salonContact || {};
  const display = c.phone_display || c.phone_link || '';
  const tel = c.phone_link || c.phone_display || '';
  const wa = (c.whatsapp_phone || c.phone_link || c.phone_display || '').replace(/[^\d]/g, '');
  return { display, tel, wa, waUrl: wa ? `https://wa.me/${wa}` : '' };
}

function showApiUnavailablePopup() {
  const t = window.translations || {};
  const c = _adminContact();
  const adminPhone = c.display;
  const whatsappUrl = c.waUrl;

  const overlay = document.createElement('div');
  overlay.className = 'api-unavailable-overlay';
  overlay.innerHTML = `
    <div class="api-unavailable-popup">
      <button class="api-unavailable-close" aria-label="Close">&times;</button>
      <h3 class="api-unavailable-title">${t.apiUnavailableTitle || 'Bonjour!'}</h3>
      <p class="api-unavailable-text">${t.apiUnavailableText || 'Online registration is temporarily unavailable. We apologize for the inconvenience — we are already fixing everything.'}</p>
      <p class="api-unavailable-text">${t.apiUnavailableContact || 'Please write to us on WhatsApp — we will quickly process your registration, or call us'} ${c.tel ? `<a href="tel:${c.tel}" class="api-unavailable-phone">${adminPhone}</a>` : ''}</p>
      ${whatsappUrl ? `<a href="${whatsappUrl}" target="_blank" rel="noopener" class="api-unavailable-whatsapp-btn">
        <img src="/static/img/whatsappIcon.svg" alt="WhatsApp" class="api-unavailable-whatsapp-icon">
        ${t.apiUnavailableWhatsApp || 'Register on WhatsApp'}
      </a>` : ''}
    </div>
  `;

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove();
  });
  overlay.querySelector('.api-unavailable-close').addEventListener('click', () => overlay.remove());

  document.body.appendChild(overlay);
}


/************************************
 * Вспомогательные функции для загрузки JSON
 ************************************/
async function loadJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Ошибка загрузки ${url}`);
  return response.json();
}

async function loadEmployees() {
  const salon = window.salon || 'l1';
  return await loadJSON(`/api/salons/${salon}/employees`);
}

async function loadFreeTime() {
  const salon = window.salon || 'l1';
  return await loadJSON(`/api/salons/${salon}/freetime`);
}

function findCategoryByName(categoryName, categories) {
  return categories.find(cat => cat.name === categoryName);
}


/************************************
 * Функция удаления кнопок "Удалить" из HTML (оставлена для совместимости)
 ************************************/
function removeDeleteButtons(htmlString) {
  const container = document.createElement("div");
  container.innerHTML = htmlString;
  const deleteButtons = container.querySelectorAll(".btn-delete");
  deleteButtons.forEach(btn => btn.remove());
  return container.innerHTML;
}


/************************************
 * Преобразует строку "HH:MM" в количество минут с полуночи
 ************************************/
function timeToMinutes(timeStr) {
  const [hours, minutes] = timeStr.split(':').map(Number);
  return hours * 60 + minutes;
}

/************************************
 * Проверяет, что начиная с индекса startIndex в массиве slots есть requiredSlots подряд
 ************************************/
function isConsecutive(slots, startIndex, requiredSlots) {
  if (startIndex + requiredSlots > slots.length) return false;
  const startTime = timeToMinutes(slots[startIndex]);
  for (let j = 1; j < requiredSlots; j++) {
    const expected = startTime + 30 * j;
    const actual = timeToMinutes(slots[startIndex + j]);
    if (actual !== expected) return false;
  }
  return true;
}

/************************************
 * Вспомогательные функции для работы с датами и слотами
 ************************************/
function normalizeDateKey(dateKey) {
  if (!dateKey || typeof dateKey !== 'string') return null;
  const match = dateKey.match(/^\d{4}-\d{1,2}-\d{1,2}/);
  if (match && match[0]) {
    return match[0];
  }
  const parts = dateKey.split(/[T\s]/);
  return parts.length > 0 ? parts[0] : null;
}

function dateKeyToNumber(dateKey) {
  const normalized = normalizeDateKey(dateKey);
  if (!normalized) return NaN;
  const parts = normalized.split('-');
  if (parts.length !== 3) return NaN;
  const [yearStr, monthStr, dayStr] = parts;
  const year = parseInt(yearStr, 10);
  const month = parseInt(monthStr, 10);
  const day = parseInt(dayStr, 10);
  if ([year, month, day].some(num => Number.isNaN(num))) return NaN;
  return year * 10000 + month * 100 + day;
}

function getSlotsForDate(empFree, date) {
  if (!empFree || !date) return null;
  if (empFree[date]) return empFree[date];
  const normalizedDate = normalizeDateKey(date);
  if (!normalizedDate) return null;
  if (empFree[normalizedDate]) return empFree[normalizedDate];
  const matchingKey = Object.keys(empFree).find(key => normalizeDateKey(key) === normalizedDate);
  return matchingKey ? empFree[matchingKey] : null;
}

function hasRequiredSlotsOnDate(empFree, date, requiredSlots = 1) {
  const slots = getSlotsForDate(empFree, date);
  if (!slots || slots.length === 0) return false;
  for (let i = 0; i < slots.length; i++) {
    if (isConsecutive(slots, i, requiredSlots)) {
      return true;
    }
  }
  return false;
}

/************************************
 * Генерирует массив временных слотов начиная с startSlot с шагом 30 минут
 ************************************/
function generateTimeSlots(startSlot, requiredSlots) {
  const result = [];
  const [hours, minutes] = startSlot.split(':').map(Number);
  let totalMinutes = hours * 60 + minutes;
  for (let i = 0; i < requiredSlots; i++) {
    let currentMinutes = totalMinutes + i * 30;
    let hh = Math.floor(currentMinutes / 60);
    let mm = currentMinutes % 60;
    result.push(String(hh).padStart(2, '0') + ':' + String(mm).padStart(2, '0'));
  }
  return result;
}

async function loadReservedSlots() {
  try {
    const resp = await fetch('/reserved_slots');
    if (!resp.ok) return {};
    return await resp.json();
  } catch (err) {
    console.error('Failed to load reserved slots', err);
    return {};
  }
}

async function reserveSlotOnServer(empId, date, time) {
  const resp = await fetch('/reserve_slot', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ employeeId: empId, date: date, time: time })
  });
  return resp.ok;
}

function releaseSlotOnServer(empId, date, time) {
  const payload = JSON.stringify({ employeeId: empId, date: date, time: time });
  if (navigator.sendBeacon) {
    navigator.sendBeacon('/release_slot', payload);
    return Promise.resolve();
  }
  return fetch('/release_slot', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload
  });
}

function startReservationTimer() {
  const timerEl = document.getElementById('reservationTimer');
  reservationEndTime = Date.now() + 5 * 60 * 1000;
  timerEl.style.display = 'block';

  function pad(n) {
    return n.toString().padStart(2, '0');
  }

  function tick() {
    const remaining = reservationEndTime - Date.now();
    if (remaining <= 0) {
      clearInterval(reservationInterval);
      reservationInterval = null;
      timerEl.textContent = window.translations['timeExpiredText'] || 'Time expired';
      releaseAllReservedSlots();
      return;
    }
    const m = Math.floor(remaining / 60000);
    const s = Math.floor((remaining % 60000) / 1000);
    timerEl.textContent = (window.translations['timeLeftText'] || 'Time left') + ': ' + pad(m) + ':' + pad(s);
  }

  clearInterval(reservationInterval);
  tick();
  reservationInterval = setInterval(tick, 1000);
}

function releaseAllReservedSlots() {
  reservedSlotsInfo.forEach(info => {
    releaseSlotOnServer(info.employeeId, info.date, info.time);
  });
  reservedSlotsInfo = [];
  const timerEl = document.getElementById('reservationTimer');
  if (timerEl) timerEl.style.display = 'none';
}

/************************************
 * Функция для форматирования даты в локальном формате YYYY-MM-DD
 ************************************/
function formatLocalDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/************************************
 * Функция для поиска ближайшей свободной даты для сотрудника
 ************************************/
function getNextAvailableDate(empFree, requiredSlots = 1, fromDate = null) {
  if (!empFree) return null;
  
  const fromDateProvided = !!fromDate;
  const fromDateNumberRaw = fromDateProvided ? dateKeyToNumber(fromDate) : NaN;
  const hasFromDateNumber = fromDateProvided && !Number.isNaN(fromDateNumberRaw);
  const fromDateNumber = hasFromDateNumber ? fromDateNumberRaw : null;
  const normalizedFromDate = fromDateProvided ? normalizeDateKey(fromDate) : null;
  const useNormalizedFallback = normalizedFromDate && !hasFromDateNumber;

  const dates = Object.keys(empFree).sort((a, b) => {
    const aNumber = dateKeyToNumber(a);
    const bNumber = dateKeyToNumber(b);
    const aHasNumber = !Number.isNaN(aNumber);
    const bHasNumber = !Number.isNaN(bNumber);

    if (aHasNumber && bHasNumber) {
      if (aNumber !== bNumber) {
        return aNumber - bNumber;
      }
      return a.localeCompare(b);
    }
    if (aHasNumber) return -1;
    if (bHasNumber) return 1;
    return a.localeCompare(b);
  });
  for (let d of dates) {
    const dNumber = dateKeyToNumber(d);
    if (fromDateNumber !== null && !Number.isNaN(dNumber) && dNumber < fromDateNumber) {
      continue;
    }
    if (useNormalizedFallback) {
      const normalizedDateKey = normalizeDateKey(d);
      if (normalizedDateKey && normalizedDateKey < normalizedFromDate) {
        continue;
      }
    }
    const slots = empFree[d];
    if (!slots || slots.length === 0) continue;
    for (let i = 0; i < slots.length; i++) {
      if (isConsecutive(slots, i, requiredSlots)) {
        return d;
      }
    }
  }
  return null;
}

/************************************
 * Функция обновления общей суммы услуг в корзине
 ************************************/
function updateCartTotal() {
  const cartItemsContainer = document.getElementById("cartItemsContainer");
  const cartTotalEl = document.getElementById("cartTotal");
  let total = 0;
  let itemsCount = 0;
  
  if (cartItemsContainer) {
    const items = cartItemsContainer.querySelectorAll(".cartItem");
    itemsCount = items.length;
    items.forEach(item => {
      const priceText = item.querySelector(".cartServicePrice")?.textContent || "0";
      const price = parseFloat(priceText.replace(/[^\d\.]/g, ""));
      total += isNaN(price) ? 0 : price;
    });
  }
  
  if (cartTotalEl) {
    if (total === 0) {
      cartTotalEl.style.display = "none";
    } else {
      cartTotalEl.style.display = "flex";
      cartTotalEl.innerHTML = `<div class="borderLine"></div>
      <div class="totalText">
        <p class="totalSumText">${window.translations["totalText"]}</p>
        <p class="totalSumNum">${(window._currencySymbol ? window._currencySymbol() : '£')} ${total}</p>
      </div>
      <button class="btn-confirm-cart">${window.translations["orderCartBtnModal"]}</button>
      </div>`;
    }
  }
  
  const cartEmptyEl = document.getElementById("cartEmpty");
  const cartFilledEl = document.getElementById("cartFilled");
  if (itemsCount === 0) {
    if (cartEmptyEl) {
      cartEmptyEl.style.display = "flex";
      cartEmptyEl.style.flexDirection = "column";
    }
    if (cartFilledEl) cartFilledEl.style.display = "none";
  } else {
    if (cartEmptyEl) cartEmptyEl.style.display = "none";
    if (cartFilledEl) {
      cartFilledEl.style.display = "flex";
      cartFilledEl.style.flexDirection = "column";
    } 
  }

  refreshConfirmationScreen();
}

/************************************
 * Функция добавления услуги в корзину
 ************************************/
function addServiceToCart(serviceInfo) {
  cartData.push(serviceInfo);
  renderCart();
}

function renderCart() {
  const cartItemsContainer = document.getElementById("cartItemsContainer");
  cartItemsContainer.innerHTML = "";

  cartData.forEach(serviceInfo => {
    const cartItem = document.createElement("div");
    cartItem.classList.add("cartItem");
    cartItem.setAttribute("data-service-id", serviceInfo.id);

    
    cartItem.innerHTML = `
      <div class="choiceService">
        <p>${window.translations["serviceCatText"]} ${extractTextByLang(serviceInfo.category, window.appLang)}</p>
        <button class="btn-delete">
          <img src="${trashUrl}" width="16px" height="16px" alt="Delete">
        </button>
      </div>
      <div class="cartItemBlock">
        <div class="service-info-block">
          <div class="serviceInfo">
            <img src="${addServiceIcon}" alt="Service Icon">
            <div class="textServiceLine">
              <span class="cartServiceName">${serviceInfo.name}</span>
              <span class="cartServiceDuration">${serviceInfo.duration}</span>
            </div>
          </div>
          <div class="serviceInfo">
            <img src="${serviceInfo.employeePhoto ? serviceInfo.employeePhoto : avatar}" alt="Master Photo" style="width:40px; height:40px; border-radius:50%;">
            <div class="textServiceLine">
              <span class="cartMasterName">${serviceInfo.master}</span>
              <span class="cartMasterPosition">${serviceInfo.positionNames}</span>
            </div>
          </div>
          <div class="serviceInfo">
            <img src="${dateTimeServiceIcon}" alt="DateTime Icon">
            <div class="textServiceLine">
              <span class="cartServiceDate">${new Date(serviceInfo.date).toLocaleDateString(`${window.translations["langMonth"]}`, { day: 'numeric', month: 'long' })}</span>
              <span class="cartServiceTime">${serviceInfo.time}</span>
            </div>
          </div>
          <div class="serviceInfo">
            
            <img class="priceserviceicon" src="${priceServiceIcon}" alt="Price Icon">
            <div class="textServiceLine">
                <p class="cartServicePriceText">${window.translations["costText"]}</p> 
                <span class="cartServicePrice">${serviceInfo.location_prices} ${serviceInfo.price_currency}</span>
            </div>
          </div>
        </div> 
      </div> 
    `;
    

    cartItem.querySelector(".btn-delete").addEventListener("click", () => {
      deleteCartItem(serviceInfo.id);
    });

    cartItemsContainer.appendChild(cartItem);
  });

  updateCartTotal();
}

/************************************
 * Функция обновления экрана подтверждения и сохранения его содержимого
 ************************************/
function refreshConfirmationScreen() {
  const middleSide = document.querySelector(".middleSide");
  if (!middleSide) return;
  
  const confirmationScreen = middleSide.querySelector(".confirmation-screen");
  if (confirmationScreen) {
    const serviceDetails = confirmationScreen.querySelector(".service-details");
    if (!serviceDetails) return;
    const items = serviceDetails.querySelectorAll(".cartItem");
    const itemsCount = items.length;
    
    if (itemsCount === 0) {
      if (typeof window.updateCategoryList === "function") {
        window.updateCategoryList();
      } else {
        console.error("Функция updateCategoryList не найдена");
      }
      return;
    }
    
    // Навешиваем обработчики для кнопок удаления внутри confirmation-screen
    const deleteButtons = serviceDetails.querySelectorAll(".btn-delete");
    deleteButtons.forEach(btn => {
      btn.addEventListener("click", () => {
        const cartItem = btn.closest(".cartItem");
        const serviceId = cartItem.getAttribute("data-service-id");
        deleteCartItem(serviceId);
      });
    });
    
    const globalActions = middleSide.querySelector(".global-actions");
    if (globalActions) {
      globalActions.style.display = "flex";
    }
    
    // Сохраняем текущее содержимое confirmation-screen (весь middleSide)
    window.fullConfirmationHTML = middleSide.innerHTML;
  }
}

/************************************
 * Функция навешивания обработчиков на свободные слоты
 ************************************/
function attachSlotClickHandlers(container, service, selectedDate) {
  const slotElements = container.querySelectorAll('.free-slot');
  slotElements.forEach(slotEl => {
    slotEl.addEventListener('click', async function() {
      const chosenSlot = this.getAttribute("data-slot");
      const employeeName = this.getAttribute("data-employee-name");
      const employeeId = this.getAttribute("data-employee-id");
      const employeePhone = this.getAttribute("data-employee-phone");
      const positionNames = this.getAttribute("data-position-names");
      const employeePhoto = this.getAttribute("data-employee-photo") || "";

      // Отримуємо конкретний варіант послуги для цього майстра (якщо є)
      let actualService = service;
      const variantDataJson = this.getAttribute("data-service-variant");
      if (variantDataJson) {
        try {
          const variantData = JSON.parse(decodeURIComponent(variantDataJson));
          actualService = {
            ...service,
            id: variantData.id,
            name: variantData.name,
            duration: variantData.duration,
            location_prices: variantData.location_prices,
            price_currency: variantData.price_currency,
            description: variantData.description,
            location_position: variantData.location_position,
            category: variantData.category,
            level: variantData.level,
            levelLabel: variantData.levelLabel
          };
        } catch (e) {
          console.error("Помилка парсингу варіанту послуги:", e);
        }
      }

      // Перераховуємо requiredSlots для конкретного варіанту (крок 30 хв)
      const actualDuration = parseInt(actualService.duration) || 30;
      const actualRequiredSlots = Math.ceil(actualDuration / 30);

      const reserved = await reserveSlotOnServer(employeeId, selectedDate, chosenSlot);
      if (!reserved) {
        alert(window.translations['slotReservedError'] || 'Slot already reserved');
        return;
      }

      reservedSlotsInfo.push({ employeeId, date: selectedDate, time: chosenSlot });
      if (!reservationInterval) {
        startReservationTimer();
      }

      addServiceToCart({
        id: actualService.id,
        name: actualService.name,
        category: actualService.category,
        duration: actualService.duration,
        location_prices: actualService.location_prices,
        price_currency: actualService.price_currency,
        description: actualService.description,
        location_position: actualService.location_position,
        level: actualService.level,
        levelLabel: actualService.levelLabel,
        master: employeeName,
        date: selectedDate,
        time: chosenSlot,
        employeeId: employeeId,
        employeePhone: employeePhone,
        positionNames: positionNames,
        requiredSlots: actualRequiredSlots,
        price: actualService.location_prices,
        employeePhoto: employeePhoto
      });

      if (
        window.freeTimeData &&
        window.freeTimeData[employeeId] &&
        window.freeTimeData[employeeId][selectedDate]
      ) {
        const empSlots = window.freeTimeData[employeeId][selectedDate];
        const slotIndex = empSlots.indexOf(chosenSlot);
        if (slotIndex !== -1) {
          empSlots.splice(slotIndex, actualRequiredSlots);
        }
      }

      const cartContent = document.getElementById("cartItemsContainer").innerHTML;
      const confirmationHTML = `
          <div class="confirmation-screen">
            <div class="service-details">
              ${cartContent}
            </div>
          </div>
        `;

      document.querySelector('.categoryList').style.display = "none";
      document.querySelector('.rightSide').style.display = "none";

      const globalActions = document.createElement("div");
      globalActions.classList.add("global-actions");
      globalActions.innerHTML = `
        <button class="btn-confirm-global">${window.translations["bookBtn"]}</button>
        <div class="btn-add-more-global">
          <img src="${addServiceMore}" alt="Add Service More">
          <p>${window.translations["addServiceBtn"]}</p>
        </div>
      `;

      const viewContainer = document.querySelector(".middleSide");
      if (viewContainer) {
        viewContainer.innerHTML = confirmationHTML;
        viewContainer.appendChild(globalActions);
        refreshConfirmationScreen();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }

      const addMoreBtn = document.querySelector(".btn-add-more-global");
      if (addMoreBtn) {
        addMoreBtn.addEventListener("click", function() {
          const bookingContainer = document.querySelector('.booking');
          if (bookingContainer) {
            bookingContainer.classList.remove('confirmation-mode');
          }
          document.querySelector('.leftSide').style.display = "flex";
          document.querySelector('.categoryList').style.display = "flex";
          document.querySelector('.rightSide').style.display = "flex";
          if (typeof window.updateCategoryList === "function") {
            window.updateCategoryList();
          } else {
            console.error("Функция updateCategoryList не найдена");
          }
        });
      }
    });
  });
}

/************************************
 * Функция обновления списка сотрудников и свободных слотов
 * Тепер з підтримкою варіантів послуг (різні рівні майстрів)
 ************************************/
async function updateEmployeesList(service, selectedDate, container) {
  try {
    const employees = await loadEmployees();
    let freeTime = window.freeTimeData || await loadFreeTime();
    window.freeTimeData = freeTime;
    const reserved = await loadReservedSlots();
    Object.keys(reserved).forEach(empId => {
      if (freeTime[empId]) {
        Object.keys(reserved[empId]).forEach(d => {
          if (freeTime[empId][d]) {
            freeTime[empId][d] = freeTime[empId][d].filter(t => !reserved[empId][d].includes(t));
          }
        });
      }
    });

    // Отримуємо варіанти послуги (якщо є)
    const variants = service.variants || [service];
    // Використовуємо передане значення hasVariants, а не просто кількість
    const hasVariants = service.hasVariants === true;

    // Знаходимо всіх майстрів для всіх варіантів послуги
    const employeeVariantMap = new Map(); // empId -> { emp, variants: [{ variant, position }] }

    variants.forEach(variant => {
      const matchingEmps = employees.filter(emp =>
        emp.positions.includes(variant.location_position)
      );
      matchingEmps.forEach(emp => {
        if (!employeeVariantMap.has(emp.id)) {
          employeeVariantMap.set(emp.id, { emp, variants: [] });
        }
        employeeVariantMap.get(emp.id).variants.push({
          variant,
          position: variant.location_position
        });
      });
    });

    const matchingEmployeesData = Array.from(employeeVariantMap.values());

    if (matchingEmployeesData.length === 0) {
      container.innerHTML = `<p>${window.translations["masterNotFound"]}</p>`;
      return;
    }

    // Визначаємо мінімальну тривалість для розрахунку слотів (крок 30 хв)
    const minDuration = Math.min(...variants.map(v => parseInt(v.duration) || 30));
    const requiredSlots = Math.ceil(minDuration / 30);

    const _c = _adminContact();
    const callBtn = _c.tel ? `<a class="call-admin-call" href="tel:${_c.tel}"><img src="${callme}" alt="call"><span>${window.translations["callAdminBtnName"]}</span></a>` : '';
    const waBtn = _c.waUrl ? `<a class="call-admin-whatsapp" href="${_c.waUrl}" target="_blank" rel="noopener"><img class="whatsapp-icon" src="${whatsappIcon}" alt="WhatsApp"></a>` : '';
    const contactBlock = (callBtn || waBtn)
      ? `<p class="contactAdminText">${window.translations["contactAdminText"]}</p><div class="call-admin-btn">${callBtn}${waBtn}</div>`
      : '';

    // Сортуємо майстрів
    matchingEmployeesData.sort((a, b) => {
      const aFree = freeTime[a.emp.id];
      const bFree = freeTime[b.emp.id];
      const aHasToday = hasRequiredSlotsOnDate(aFree, selectedDate, requiredSlots);
      const bHasToday = hasRequiredSlotsOnDate(bFree, selectedDate, requiredSlots);

      if (aHasToday && !bHasToday) return -1;
      if (bHasToday && !aHasToday) return 1;

      const aNextDate = getNextAvailableDate(aFree, requiredSlots, selectedDate);
      const bNextDate = getNextAvailableDate(bFree, requiredSlots, selectedDate);

      const aNextNumber = dateKeyToNumber(aNextDate);
      const bNextNumber = dateKeyToNumber(bNextDate);
      const aHasNumber = !Number.isNaN(aNextNumber);
      const bHasNumber = !Number.isNaN(bNextNumber);

      if (aHasNumber && bHasNumber && aNextNumber !== bNextNumber) {
        return aNextNumber - bNextNumber;
      }
      if (aHasNumber && !bHasNumber) return -1;
      if (bHasNumber && !aHasNumber) return 1;

      if (aNextDate && bNextDate) {
        const localeCompareResult = aNextDate.localeCompare(bNextDate);
        if (localeCompareResult !== 0) {
          return localeCompareResult;
        }
      }
      if (aNextDate && !bNextDate) return -1;
      if (bNextDate && !aNextDate) return 1;
      return 0;
    });

    let html = "";
    matchingEmployeesData.forEach(({ emp, variants: empVariants }) => {
      // Визначаємо пріоритет рівня майстра (вищий рівень = вищий пріоритет)
      const levelPriority = { 'art': 4, 'top': 3, 'junior': 2, 'master': 1 };

      // Сортуємо за рівнем (вищий рівень першим), а при однаковому рівні - за ціною
      const sortedVariants = empVariants.sort((a, b) => {
        const aLevel = levelPriority[a.variant.level] || 1;
        const bLevel = levelPriority[b.variant.level] || 1;
        if (aLevel !== bLevel) {
          return bLevel - aLevel; // Вищий рівень першим
        }
        return (a.variant.location_prices || 0) - (b.variant.location_prices || 0);
      });
      const primaryVariant = sortedVariants[0].variant;
      const empRequiredSlots = Math.ceil((parseInt(primaryVariant.duration) || 30) / 30);

      // Формуємо інформацію про рівень та ціну
      let levelPriceInfo = '';
      if (hasVariants && primaryVariant.levelLabel) {
        levelPriceInfo = `<span class="employee-level-price">${primaryVariant.levelLabel} - ${primaryVariant.location_prices} ${(window._currencySymbol ? window._currencySymbol() : '£')}</span>`;
      } else if (hasVariants) {
        levelPriceInfo = `<span class="employee-level-price">${primaryVariant.location_prices} ${(window._currencySymbol ? window._currencySymbol() : '£')}</span>`;
      }

      html += `<div class="employee" data-phone="${emp.phone[0]}" data-id="${emp.id}" data-position-names="${emp.position_names[0]}">`;
      const photoUrl = emp.photo ? emp.photo : avatar;
      html += `<div class="employeeViewRows">
          <img src="${photoUrl}" alt="${emp.name}" class="employee-photo">
          <div class="employee-info">
            <span class="employee-name" style="font-weight: bold;">${emp.name}</span>
            ${levelPriceInfo}
          </div>
        </div>`;

      const empFree = freeTime[emp.id];
      const slots = getSlotsForDate(empFree, selectedDate);

      // Зберігаємо дані варіанту для передачі на слоти
      const variantDataJson = encodeURIComponent(JSON.stringify({
        id: primaryVariant.id,
        name: primaryVariant.name,
        duration: primaryVariant.duration,
        location_prices: primaryVariant.location_prices,
        price_currency: primaryVariant.price_currency,
        description: primaryVariant.description,
        location_position: primaryVariant.location_position,
        category: primaryVariant.category,
        level: primaryVariant.level,
        levelLabel: primaryVariant.levelLabel
      }));

      if (slots && slots.length > 0) {
        let slotButtons = "";
        for (let i = 0; i < slots.length; i++) {
          if (isConsecutive(slots, i, empRequiredSlots)) {
            slotButtons += `<li class="free-slot"
              data-slot="${slots[i]}"
              data-employee-id="${emp.id}"
              data-employee-name="${emp.name}"
              data-employee-phone="${emp.phone[0]}"
              data-position-names="${emp.position_names[0]}"
              data-employee-photo="${emp.photo ? emp.photo : avatar}"
              data-service-variant="${variantDataJson}"
              style="cursor:pointer;">
              ${slots[i]}
            </li>`;
          }
        }
        if (slotButtons) {
          html += `<div class="employee-slots"><ul style="list-style: none; padding-left:0;">${slotButtons}</ul></div>`;
        } else {
          const nextDate = getNextAvailableDate(empFree, empRequiredSlots, selectedDate);
          if (!nextDate) {
            html += `<div class="employee-no-slots redText"><p>${window.translations["employeeNotAvailable"]}</p>${contactBlock}</div>`;
          } else {
            const dateObj = new Date(nextDate);
            const options = { day: 'numeric', month: 'long' };
            const formattedDate = dateObj.toLocaleDateString(`${window.translations["langMonth"]}`, options);
            html += `<div class="employee-no-slots">
                <p class="nextDateText">${window.translations["closestDateText"]}</p>
                <button class="btn-next-date" data-next-date="${nextDate}" style="cursor:pointer;">${formattedDate}</button>
                ${contactBlock}
              </div>`;
          }
        }
      } else {
        const nextDate = getNextAvailableDate(empFree, empRequiredSlots, selectedDate);
        if (!nextDate) {
          html += `<div class="employee-no-slots"><p>${window.translations["employeeNotAvailable"]}</p>${contactBlock}</div>`;
        } else {
          const dateObj = new Date(nextDate);
          const options = { day: 'numeric', month: 'long' };
          const formattedDate = dateObj.toLocaleDateString(`${window.translations["langMonth"]}`, options);
          html += `<div class="employee-no-slots">
              <p class="nextDateText">${window.translations["closestDateText"]}</p>
              <button class="btn-next-date" data-next-date="${nextDate}" style="cursor:pointer;">${formattedDate}</button>
              ${contactBlock}
            </div>`;
        }
      }

      html += `</div>`;
    });
    container.innerHTML = html;
    attachSlotClickHandlers(container, service, selectedDate);
  } catch (err) {
    console.error(err);
    container.innerHTML = `<p>Ошибка загрузки данных сотрудников или свободного времени.</p>`;
  }
}

/************************************
 * Обработчик клика на кнопку service-add
 ************************************/
document.addEventListener("DOMContentLoaded", () => {
  document.body.addEventListener("click", async (e) => {
    const button = e.target.closest(".service-add");
    if (button) {
      // Получаем родительский блок с данными услуги
      const serviceRow = button.closest(".service-row");
      if (serviceRow) {
        const categoryName = serviceRow.getAttribute("data-category");
        if (categoryName) {
          const cat = findCategoryByName(categoryName, filteredCategories);
          if (cat) {
            window.currentCategory = cat;
          } else {
            console.warn("Категория не найдена по имени: " + categoryName);
          }
        } else {
          console.warn("data-category не установлен в service-row");
        }
      }
  
      // Извлекаем данные услуги
      const durationValue = button.getAttribute("data-duration") || "";

      // Перевіряємо чи є варіанти послуги (різні рівні майстрів)
      const hasVariants = button.getAttribute("data-has-variants") === "true";
      let variants = null;
      if (hasVariants) {
        try {
          const variantsJson = button.getAttribute("data-variants");
          if (variantsJson) {
            variants = JSON.parse(decodeURIComponent(variantsJson));
          }
        } catch (e) {
          console.error("Помилка парсингу варіантів послуги:", e);
        }
      }

      const service = {
        id: button.getAttribute("data-id"),
        name: button.getAttribute("data-service-name"),
        duration: durationValue, // Передаємо числове значення, не строку з "хв"
        location_prices: button.getAttribute("data-location_prices"),
        price_currency: button.getAttribute("data-price_currency"),
        description: button.getAttribute("data-description"),
        location_position: button.getAttribute("data-location-position"),
        category: serviceRow.getAttribute("data-category"),
        hasVariants: hasVariants,
        variants: variants
      };
  
      // Показываем спиннер с сообщением, чтобы пользователь понимал, что идёт поиск свободных дат
      const viewContainer = document.querySelector(".middleSide");
      if (viewContainer) {
        viewContainer.innerHTML = `
          <div class="spinner-container" style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; margin-top: 50px;">
            <div class="spinner"></div>
            <p>${window.translations["loadTimeSlotsText"]}</p>
          </div>
        `;
      }
  
      // Оновлюємо freetime тільки для активного салону (швидко: 1 CRM call замість 18)
      try {
        const currentSalon = window.salon || 'l1';
        const response = await fetch(`/update_freetime?salon=${encodeURIComponent(currentSalon)}`);
        if (!response.ok) {
          throw new Error("Ошибка обновления свободного времени");
        }
        const result = await response.json();
        console.log("Free time update result:", result);
      } catch (error) {
        console.error("Ошибка при обновлении свободного времени:", error);
        hideLoadingOverlay();
        showApiUnavailablePopup();
        return;
      }
  
      // После обновления данных создаём экран выбора даты и времени (appointment-container)
  
      // Создаём контейнер календаря с input для flatpickr
      const calendarContainerEl = document.createElement('div');
      calendarContainerEl.classList.add('calendar-container');
  
      const dateInput = document.createElement('input');
      dateInput.type = 'text';
      dateInput.classList.add('flatpickr-input');
      dateInput.style.display = "none";
      calendarContainerEl.appendChild(dateInput);
  
      // Устанавливаем дату по умолчанию (сегодня)
      const today = new Date();
      const todayFormatted = formatLocalDate(today);
  
      // Инициализируем flatpickr с необходимыми опциями
      const fpInstance = flatpickr(dateInput, {
        dateFormat: "Y-m-d",
        defaultDate: today,
        minDate: "today",
        inline: true,
        locale: { firstDayOfWeek: 1 },
        onChange: async function(selectedDates, dateStr) {
          console.log("Выбрана дата: " + dateStr);
          await updateEmployeesList(service, dateStr, employeesListContainer);
        }
      });
      window.currentFlatpickr = fpInstance;
  
      // Создаём контейнер для списка сотрудников
      const employeesListContainer = document.createElement('div');
      employeesListContainer.classList.add('employees-list-container');
  
      // Собираем основной контейнер для выбора даты и времени
      const appointmentContainer = document.createElement('div');
      appointmentContainer.classList.add('appointment-container');
      appointmentContainer.style.display = "flex";
  
      // Создаём заголовок для блока выбора даты и времени
      const appointmentTitle = document.createElement('div');
      appointmentTitle.classList.add('appointment-title');
      appointmentTitle.innerHTML = `<img class="backTo" id="backTo1" src="${backTo}"><p>${window.translations["dateTimeTitle"]}</p>`;
  
      // Левая колонка: календарь
      const leftColumn = document.createElement('div');
      leftColumn.classList.add('leftColumn');
      leftColumn.appendChild(appointmentTitle);
      leftColumn.appendChild(calendarContainerEl);
  
      // Обработчик для кнопки "Назад" — повертаємо на список послуг тієї ж
      // категорії. Викликаємо selectCategory (full flow з ensureCategoryServices)
      // замість локального innerHTML rebuild — щоб category з не-завантаженими
      // services не давала пустий екран.
      const backTo1 = appointmentTitle.querySelector("#backTo1");
      if (backTo1) {
        backTo1.addEventListener("click", function() {
          const cat = window.currentCategory;
          if (!cat) return;
          if (window.innerWidth <= 769) {
            const left = document.querySelector('.leftSide');
            if (left) left.style.display = "flex";
          }
          const middleSide = document.querySelector(".middleSide");
          if (middleSide) {
            middleSide.innerHTML = `
              <h2 class="service-title">Послуга</h2>
              <div class="hairLengthSelector" id="hairLengthSelector" style="display: none;">
                <h3>ОБЕРІТЬ ВАШУ ДОВЖИНУ ВОЛОССЯ</h3>
                <div class="hairLengthOptions" id="hairLengthOptions"></div>
              </div>
              <div id="hairLengthDropdownContainer" style="display:none;"></div>
              <div class="services-block" id="servicesList"></div>
            `;
          }
          if (typeof window.selectCategory === 'function') {
            window.selectCategory(cat.id);
          } else if (typeof window.renderAndShowCategory === 'function') {
            window.renderAndShowCategory(cat);
          }
        });
      }
  
      // Правая колонка: список сотрудников
      const rightColumn = document.createElement('div');
      rightColumn.classList.add('rightColumn');
      rightColumn.appendChild(employeesListContainer);
  
      appointmentContainer.appendChild(leftColumn);
      appointmentContainer.appendChild(rightColumn);
  
      if (window.innerWidth <= 769) {
        document.querySelector('.leftSide').style.display = "none";
      }
  
      // Заменяем содержимое центральной колонки (spinner) на appointment-container
      if (viewContainer) {
        viewContainer.innerHTML = "";
        viewContainer.appendChild(appointmentContainer);
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
  
      // Инициализируем список сотрудников для даты по умолчанию (сегодня)
      updateEmployeesList(service, todayFormatted, employeesListContainer);
    }
  });
});


/************************************
 * Функция инициализации корзины (отрисовка пустой корзины)
 ************************************/
function renderEmptyCart() {
  const cartBox = document.getElementById("cartBox");
  if (cartBox) {
    cartBox.innerHTML = `
      <div class="cartEmpty" id="cartEmpty">
        <div class="previewCart">
          <p class="cartEmptyText">${window.translations["emptyCartText"]}</p>
        </div>
        <button class="previewCart-btn">${window.translations["orderCartBtn"]}</button>
      </div>
      <div class="cartFilled" id="cartFilled" style="display: none;">
        <div class="cartItems" id="cartItemsContainer"></div>
        <div class="cartTotal" id="cartTotal"></div>
      </div>
    `;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  renderEmptyCart();
  updateCartTotal();
});

document.addEventListener('click', function(e) {
  if (e.target.classList.contains('btn-next-date')) {
    const nextDate = e.target.getAttribute("data-next-date");
    if (window.currentFlatpickr && nextDate) {
      const newDate = new Date(nextDate);
      window.currentFlatpickr.setDate(newDate, true);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }
});

/************************************
 * Функция отображения экрана оформления заявки
 ************************************/
function showBookingForm(savedCartHTML) {
  // Получаем центральный контейнер (bookingSection)
  const bookingSection = document.querySelector(".middleSide");
  if (!bookingSection) return;

  // Скрываем боковые блоки
  document.querySelector('.rightSide').style.display = "none";
  document.querySelector('.categoryList').style.display = "none";
  const bookingContainer = document.querySelector('.booking');
  if (bookingContainer) {
    bookingContainer.classList.add('confirmation-mode');
  }
  if (window.innerWidth <= 1280) {
    document.querySelector('.leftSide').style.display = "none";
  }

  // Сохраняем текущее состояние, если ещё не сохранено
  if (!window.previousBookingHTML) {
    window.previousBookingHTML = bookingSection.innerHTML;
  }
  
  // Генерируем HTML для выбранных услуг через нашу функцию.
  // Если требуется убрать кнопку удаления, применяем removeDeleteButtons.
  let rawServicesHTML = getSelectedServicesSummaryHTML();
  let processedServicesHTML = removeDeleteButtons(rawServicesHTML);
  
  // Используем processedServicesHTML, чтобы кнопки btn-delete не отображались на экране подтверждения
  let servicesSummary = `<div class="servicesSummary">
       <h3>${window.translations["totalServicesText"]}</h3>
       <div class="selected-services-summary">
         ${processedServicesHTML}
       </div>
     </div>`;
  
  // Формируем HTML экрана оформления заявки
  bookingSection.innerHTML = `
    <div class="booking-form-container">
      <div class="bookingForm-title">
        <img class="backTo" id="backTo2" src="${backTo}" width="40px" height="40px">
        <h2 class="bookingForm-titleText">${window.translations["confirmTitle"]}</h2>
      </div>
      <form id="bookingForm">
        <div class="userField formField">
          <div class="iconBox"><img src="${userIcon}" width="16px" height="16px" alt=""></div>
          <input type="text" id="customerName" name="customerName" required placeholder="Enter your name">
        </div>
        <div class="phoneField formField">
          <div class="iconBox"><img src="${phoneIcon}" width="16px" height="16px" alt=""></div>
          <input type="tel" id="customerPhone" name="customerPhone" required placeholder="Your phone number">
        </div>
        <div class="emailField formField">
          <div class="iconBox"><img src="${emailIcon}" width="16px" height="16px" alt=""></div>
          <input type="email" id="customerEmail" name="customerEmail" required placeholder="Your email">
        </div>
        <div class="callmelField formField">
          <div class="iconBox"><img src="${callme}" width="16px" height="16px" alt=""></div>
          <select id="callmeSelect">
            <option value="${window.translations["callmeOption1"]}">${window.translations["callmeOption1"]}</option>
            <option value="${window.translations["callmeOption2"]}" selected>${window.translations["callmeOption2"]}</option>
          </select>
        </div>
        <div class="policity" style="margin-bottom: 15px;">
          <input type="checkbox" id="consent" name="consent" required>
          <label class="policityLabel" for="consent">
            ${window.translations["policityText"]} 
            <a href="https://p-de-p.co.uk/privacy-policy-en/" target="_blank" class="policityLink">Privacy policy</a>
          </label>
        </div>
        <button onclick="gtag('event', 'click', { 'event_category': 'button', 'event_action': 'submit' });" type="submit" class="btn-submit">${window.translations["orderCartBtn"]}</button>
      </form>
      ${servicesSummary}
    </div>
  `;
  
  // Привязываем обработчик для кнопки "backTo" (с id backTo2)
  const backTo2Btn = document.getElementById("backTo2");
  if (backTo2Btn) {
    backTo2Btn.addEventListener("click", function() {
      if (window.previousBookingHTML) {
        bookingSection.innerHTML = window.previousBookingHTML;
        window.previousBookingHTML = null; // очищаем резервную копию
        const bookingContainer = document.querySelector('.booking');
        if (bookingContainer) {
          bookingContainer.classList.remove('confirmation-mode');
        }
        // Повертаємо видимість бокових блоків (showBookingForm їх ховав).
        // Симетрично з showBookingForm: leftSide показуємо тільки на >1280
        // (на tablet/mobile там окремий hamburger nav).
        const _r = document.querySelector('.rightSide');
        const _c = document.querySelector('.categoryList');
        const _l = document.querySelector('.leftSide');
        if (_r) _r.style.display = "flex";
        if (_c) _c.style.display = "flex";
        if (_l && window.innerWidth > 1280) _l.style.display = "flex";
        refreshConfirmationScreen();
        // Повторно привязываем обработчик для btn-add-more-global:
        const addMoreBtn = document.querySelector(".btn-add-more-global");
        if (addMoreBtn) {
          addMoreBtn.addEventListener("click", () => {
            document.querySelector('.leftSide').style.display = "flex";
            document.querySelector('.categoryList').style.display = "flex";
            document.querySelector('.rightSide').style.display = "flex";
            if (typeof window.updateCategoryList === "function") {
              window.updateCategoryList();
            } else {
              console.error("Функция updateCategoryList не найдена");
            }
          });
        }
      } else {
        console.warn("Резервная разметка для восстановления отсутствует");
      }
    });
    
  } else {
    console.warn("Элемент backTo (id 'backTo2') не найден");
  }
  
  // Инициализируем выбор кода страны и маску для телефона
  let iti = null;
  const phoneInputField = document.querySelector("#customerPhone");
  const emailInputField = document.querySelector("#customerEmail");
  const nameInputField = document.querySelector("#customerName");
  const submitBtn = document.querySelector("#bookingForm .btn-submit");
  const consentCheckbox = document.getElementById("consent");
  
  if (phoneInputField && window.intlTelInput) {
    // Initial country per-salon: GB→gb, UA→ua, PL→pl. window.salonCountry
    // приходить з backend (booking.salons.country).
    const _sc = (window.salonCountry || 'gb').toLowerCase();
    const _initCountry = (_sc === 'ua' || _sc === 'pl') ? _sc : 'gb';
    iti = window.intlTelInput(phoneInputField, {
      separateDialCode: true,
      autoPlaceholder: "aggressive",
      formatOnDisplay: true,
      initialCountry: _initCountry,
      utilsScript: "https://cdnjs.cloudflare.com/ajax/libs/intl-tel-input/17.0.19/js/utils.js"
    });

    const applyMask = () => {
      const placeholder = phoneInputField.getAttribute("placeholder") || "";
      const data = iti.getSelectedCountryData();
      let maskPattern = placeholder.replace(/\d/g, "9");
      const options = {
        mask: maskPattern,
        placeholder: "_",
        showMaskOnHover: true
      };
      if (data && data.dialCode === "44") {
        maskPattern = maskPattern.replace("9", "X");
        options.mask = maskPattern;
        options.definitions = { X: { validator: "[1-9]" } };
      }
      if (window.jQuery && jQuery.fn.inputmask) {
        jQuery(phoneInputField).inputmask(options);
      } else if (window.Inputmask) {
        Inputmask(options).mask(phoneInputField);
      }
    };

    if (iti.promise) {
      iti.promise.then(() => {
        applyMask();
        phoneInputField.addEventListener("countrychange", applyMask);
      });
    } else {
      applyMask();
      phoneInputField.addEventListener("countrychange", applyMask);
    }
  }

function isPhoneComplete() {
    if (window.jQuery && jQuery.fn.inputmask && jQuery(phoneInputField).inputmask) {
      return jQuery(phoneInputField).inputmask("isComplete");
    } else if (phoneInputField.inputmask) {
      return phoneInputField.inputmask.isComplete();
    }
    return phoneInputField.value && phoneInputField.value.indexOf("_") === -1;
  }

function isEmailValid(email) {
    return /^\S+@\S+\.\S+$/.test(email);
  }

// Normalizes phone number input per country rules
const maxPhoneDigitsByCountry = { "44": 10 };

function normalizePhoneNumber() {
    if (!iti || !phoneInputField) return;
    const data = iti.getSelectedCountryData();
    let digits = "";
    if (window.jQuery && jQuery.fn.inputmask && jQuery(phoneInputField).inputmask) {
      digits = jQuery(phoneInputField).inputmask("unmaskedvalue");
    } else if (phoneInputField.inputmask && phoneInputField.inputmask.unmaskedvalue) {
      digits = phoneInputField.inputmask.unmaskedvalue();
    } else {
      digits = phoneInputField.value.replace(/\D/g, "");
    }
    if (data && data.dialCode === "44") {
      digits = digits.replace(/^0+/, "");
    }
    const maxLen = maxPhoneDigitsByCountry[data.dialCode];
    if (maxLen) {
      digits = digits.slice(0, maxLen);
    }
    if (window.jQuery && jQuery.fn.inputmask && jQuery(phoneInputField).inputmask) {
      jQuery(phoneInputField).inputmask("setvalue", digits);
    } else if (phoneInputField.inputmask) {
      phoneInputField.inputmask.setValue(digits);
    } else {
      phoneInputField.value = digits;
    }
  }

  let showErrors = false;

  function validateBookingForm(highlightErrors = showErrors) {
    const validName = nameInputField.value.trim() !== "";
    const validPhone = isPhoneComplete();
    const validEmail = isEmailValid(emailInputField.value.trim());
    const consentOk = !consentCheckbox || consentCheckbox.checked;

    if (highlightErrors) {
      nameInputField.classList.toggle("error-field", !validName);
      phoneInputField.classList.toggle("error-field", !validPhone);
      emailInputField.classList.toggle("error-field", !validEmail);
    } else {
      nameInputField.classList.remove("error-field");
      phoneInputField.classList.remove("error-field");
      emailInputField.classList.remove("error-field");
    }

    return validName && validPhone && validEmail && consentOk;
  }

  nameInputField.addEventListener("input", () => validateBookingForm());
  // Prevent entering a leading zero for UK numbers
  phoneInputField.addEventListener("beforeinput", (e) => {
    if (!iti) return;
    const data = iti.getSelectedCountryData();
    if (data && data.dialCode === "44") {
      const digits = phoneInputField.value.replace(/\D/g, "");
      if (!digits && e.data === "0") {
        e.preventDefault();
      }
    }
  });
  phoneInputField.addEventListener("input", () => {
    setTimeout(() => {
      normalizePhoneNumber();
      validateBookingForm();
    }, 0);
  });
  phoneInputField.addEventListener("paste", () => {
    setTimeout(() => {
      normalizePhoneNumber();
      validateBookingForm();
    }, 0);
  });
  phoneInputField.addEventListener("countrychange", () => {
    normalizePhoneNumber();
    setTimeout(() => validateBookingForm(), 0);
  });
  emailInputField.addEventListener("input", () => validateBookingForm());
  if (consentCheckbox) consentCheckbox.addEventListener("change", () => validateBookingForm());

  validateBookingForm();

  // Флаг для запобігання повторним відправкам форми
  let isSubmitting = false;

  // Функція для генерації UUID на клієнті
  function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
      const r = Math.random() * 16 | 0;
      const v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    }).toUpperCase();
  }

  // Привязываем обработчик отправки формы
  const bookingForm = document.getElementById("bookingForm");
  if (bookingForm) {
    bookingForm.addEventListener("submit", function(e) {
      e.preventDefault();

      // Захист від повторних кліків
      if (isSubmitting) {
        console.log("Форма вже відправляється, ігноруємо повторний клік");
        return;
      }

      const btnSubmit = bookingForm.querySelector(".btn-submit");
      const originalContent = btnSubmit.innerHTML;
      showErrors = true;
      if (!validateBookingForm(true)) {
        alert("Please fill in all required fields correctly.");
        return;
      }

      // Блокуємо повторні відправки ДО початку fetch
      isSubmitting = true;
      btnSubmit.innerHTML = '<div class="spinner"></div>';
      btnSubmit.disabled = true;
      
      const customerName = document.getElementById("customerName").value;
      const customerPhone = iti ? iti.getNumber() : document.getElementById("customerPhone").value;
      const customerEmail = document.getElementById("customerEmail").value;
      const customercallmeSelect = document.getElementById("callmeSelect").value;
      
      
      const servicesData = cartData.map(service => ({
        serviceName: service.name,
        serviceId: service.id,
        category: service.category,
        duration: parseInt(String(service.duration).replace(/\D/g, ""), 10),
        price: service.location_prices,
        currency: service.price_currency,
        employeeId: service.employeeId,
        employeeName: service.master,
        positionNames: service.positionNames,
        employeePhone: service.employeePhone,
        employeePhoto: service.employeePhoto,
        date: service.date,
        time: service.time
      }));
      
      // Генеруємо унікальний reference на клієнті для запобігання дублям
      const bookingReference = generateUUID();

      const bookingJSON = {
        client: {
          name: customerName,
          phone: customerPhone,
          email: customerEmail,
          callme: customercallmeSelect
        },
        services: servicesData,
        lang: window.appLang,
        reference: bookingReference
      };
      
      const url = `/${window.salon}/create_appointment`;
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(bookingJSON)
      })
      .then(response => {
        console.log("HTTP статус:", response.status);
        if (!response.ok) throw new Error("Сервер вернул ошибку");
        return response.json();
      })
      .then(result => {
        console.log("Ответ от сервера:", result);
        if (result.payment && result.payment.status === "success" && result.payment.paymentLink) {
          // Payment enabled: redirect to payment page
          window.location.href = result.payment.paymentLink;
        } else if (result.payment && result.payment.status === "no_payment" && result.redirect) {
          // No payment required: redirect to success page
          window.location.href = result.redirect;
        } else {
          alert("Ошибка при создании платежной ссылки.");
          // Розблоковуємо форму при невдалому створенні платежу
          isSubmitting = false;
          btnSubmit.innerHTML = originalContent;
          btnSubmit.disabled = false;
        }
      })
      .catch(error => {
        console.error("Ошибка при создании Appointment:", error);
        alert("Ошибка при создании заявки. Попробуйте еще раз.");
        // Розблоковуємо форму тільки при помилці, щоб користувач міг спробувати знову
        isSubmitting = false;
        btnSubmit.innerHTML = originalContent;
        btnSubmit.disabled = false;
      });
    });
  }
  
  // Инициализируем обработчики для аккордеона в блоке с выбранными услугами
  initAccordionTogglesForSummary();
  
  // Сохраняем текущую разметку (без изменений) для восстановления при нажатии backTo
  window.fullConfirmationHTML = bookingSection.innerHTML;
}







function initAccordionTogglesForSummary() {
  const toggles = document.querySelectorAll(".selected-service-item .accordion-toggle");
  toggles.forEach(toggle => {
    toggle.addEventListener("click", function() {
      const parentItem = this.closest(".selected-service-item");
      if (!parentItem) return;
      const details = parentItem.querySelector(".service-summary-details");
      if (!details) return;
      if (details.style.display === "none" || details.style.display === "") {
        details.style.display = "block";
        const img = this.querySelector("img");
        if (img) {
          img.src = arrowup;
        }
      } else {
        details.style.display = "none";
        const img = this.querySelector("img");
        if (img) {
          img.src = arrowdown;
        }
      }
    });
  });
}
/************************************
 * Функция получения HTML выбранных услуг для экрана успешного оформления
 ************************************/
function getSelectedServicesSummaryHTML() {
  if (cartData.length === 0) {
    return "<p>Корзина пуста</p>";
  }
  let html = "";
  
  cartData.forEach(service => {
    html += `
      <div class="selected-service-item">
        <div class="service-summary-header">
          <div class="accordion-toggle">
            <img src="${arrowdown}" alt="Toggle Accordion">
          </div>
          <span class="cartServiceName">${service.name}</span>
          <button class="btn-delete">
            <img src="${trashUrl}" width="16px" height="16px" alt="Delete">
          </button>
        </div>
        <div class="service-summary-details" style="display: none;">
          <div class="service-info-block">
            <div class="serviceInfo">
              <img src="${addServiceIcon}" alt="Service Icon">
              <div class="textServiceLine">
                <span class="cartServiceName">${service.name}</span>
                <span class="cartServiceDuration">${service.duration}</span>
              </div>
            </div>
            <div class="serviceInfo">
              <img src="${service.employeePhoto ? service.employeePhoto : avatar}" alt="Master Photo" style="width:40px; height:40px; border-radius:50%;">
              <div class="textServiceLine">
                <span class="cartMasterName">${service.master}</span>
                <span class="cartMasterPosition">${service.positionNames}</span>
              </div>
            </div>
            <div class="serviceInfo">
              <img src="${dateTimeServiceIcon}" alt="DateTime Icon">
              <div class="textServiceLine">
                <span class="cartServiceDate">${new Date(service.date).toLocaleDateString(`${window.translations["langMonth"]}`, { day: 'numeric', month: 'long' })}</span>
                <span class="cartServiceTime">${service.time}</span>
              </div>
            </div>
            <div class="serviceInfo">
              <img class="priceserviceicon" src="${priceServiceIcon}" alt="Price Icon">
              <div class="textServiceLine">
                <p class="cartServicePriceText">${window.translations["costText"]}</p> 
                <span class="cartServicePrice">${service.location_prices} ${service.price_currency}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  });
  
  return html;
}




function showSuccessPage(customerName) {
  const bookingSection = document.querySelector(".booking");
  if (bookingSection) {
    bookingSection.style.display = "flex";
    bookingSection.style.flexDirection = "column";
    bookingSection.style.justifyContent = "center";
    bookingSection.style.alignItems = "center";
  }
  if (!bookingSection) return;
  
  const servicesSummaryHTML = removeDeleteButtons(getSelectedServicesSummaryHTML());  
  bookingSection.innerHTML = `
    <div class="success-page-container spcDesctop">
      <div class="left-column">
        <img class="salonPhotoDesctop" src="${salonPhoto}" alt="Фото салона" style="width: 100%;">
        <div class="salon-map" style="width: 100%;">
          <iframe src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d2482.7547599389413!2d-0.14319042337927976!3d51.51771507181563!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x48761b146058a8a9%3A0xe200468b46d82427!2sPIED-DE-POULE%2067%20Mortimer%20Street!5e0!3m2!1sru!2sua!4v1741038191571!5m2!1sru!2sua" width="100%" height="400px" style="border:0;" allowfullscreen="" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
        </div>
      </div>
      <div class="right-column" style="flex: 2; min-width: 300px; padding: 10px;">
        <div class="right-column-top">
          <img src="${heartIcon}" alt="Heart" style="width: 100%; max-width: 48px;">
          <h2 style="text-transform: uppercase;">Дякуємо, <span class="nameOfClient">${customerName}</span>, що обираєте нас!</h2>
          <p>Ваш візит заброньовано та ми з нетерпінням чекаємо нашої зустрічі.</p>
        </div>
        <div class="selected-services-summary">
          ${servicesSummaryHTML}
        </div>
        <div class="success-buttons">
          <button class="btn-home">На головну</button>
          <button class="btn-add-calendar">${window.translations["calendarBtnName"]}</button>
        </div>
      </div>
    </div>

    <div class="success-page-container spcPhone">
      <div class="left-column">
        <img class="salonPhotoDesctop" src="${salonPhoto}" alt="Фото салона" style="width: 100%;">
        <img class="heartIcon" src="${heartIcon}" alt="Heart" style="width: 100%; max-width: 48px;">
      </div>
      <div class="right-columnPhone">
        <div class="right-column-topPhone">
          <h2 style="text-transform: uppercase;">Дякуємо, <span class="nameOfClient">${customerName}</span>, що обираєте нас!</h2>
          <p>Ваш візит заброньовано, та ми з нетерпінням чекаємо нашої зустрічі.</p>
        </div>
        <div class="selected-services-summary">
          ${servicesSummaryHTML}
        </div>
        <div class="salon-map" style="width: 100%;">
          <iframe src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d2482.7547599389413!2d-0.14319042337927976!3d51.51771507181563!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x48761b146058a8a9%3A0xe200468b46d82427!2sPIED-DE-POULE%2067%20Mortimer%20Street!5e0!3m2!1sru!2sua!4v1741038191571!5m2!1sru!2sua" width="100%" height="400px" style="border:0;" allowfullscreen="" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
        </div>
        <div class="success-buttonsPhone">
          <button class="btn-home">На головну</button>
          <button class="btn-add-calendar">${window.translations["calendarBtnName"]}</button>
        </div>
      </div>
    </div>
  `;
  
  const btnHome = document.querySelector(".btn-home");
  if (btnHome) {
    btnHome.addEventListener("click", () => {
      window.location.href = "https://p-de-p.co.uk/";
    });
  }
  
  const formatDateForICS = dateObj => {
    const pad = num => num.toString().padStart(2, '0');
    return dateObj.getUTCFullYear().toString() +
           pad(dateObj.getUTCMonth() + 1) +
           pad(dateObj.getUTCDate()) + "T" +
           pad(dateObj.getUTCHours()) +
           pad(dateObj.getUTCMinutes()) +
           pad(dateObj.getUTCSeconds()) + "Z";
  };
      
  const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  const isAndroid = () => /Android/.test(navigator.userAgent);
      
  const downloadICSFileForEvents = events => {
    let icsContent = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Your Company//Your Product//EN\r\nCALSCALE:GREGORIAN\r\n";
    events.forEach(event => {
      icsContent += "BEGIN:VEVENT\r\n";
      icsContent += "SUMMARY:" + event.title + "\r\n";
      icsContent += "DTSTART:" + event.start + "\r\n";
      icsContent += "DTEND:" + event.end + "\r\n";
      icsContent += "LOCATION:" + event.location + "\r\n";
      icsContent += "DESCRIPTION:" + event.description + "\r\n";
      icsContent += "END:VEVENT\r\n";
    });
    icsContent += "END:VCALENDAR";
    const blob = new Blob([icsContent], { type: "text/calendar;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "booking_events.ics";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };
      
  const openGoogleCalendar = event => {
    const start = event.start.replace(/[-:]/g, "");
    const end = event.end.replace(/[-:]/g, "");
    const url = "https://calendar.google.com/calendar/r/eventedit?" +
      "text=" + encodeURIComponent(event.title) +
      "&dates=" + start + "/" + end +
      "&details=" + encodeURIComponent(event.description) +
      "&location=" + encodeURIComponent(event.location);
    window.open(url, "_blank");
  };
      
  const btnAddCalendar = document.querySelector(".btn-add-calendar");
  if (btnAddCalendar) {
    btnAddCalendar.addEventListener("click", () => {
      const originalContent = btnAddCalendar.innerHTML;
      btnAddCalendar.innerHTML = '<div class="spinner"></div>';
      btnAddCalendar.disabled = true;
      
      if (!cartData || cartData.length === 0) {
        alert("Нет оформленных услуг для добавления в календарь.");
        btnAddCalendar.innerHTML = originalContent;
        btnAddCalendar.disabled = false;
        return;
      }
      
      const events = cartData.map(service => {
        const startDate = new Date(service.date + "T" + service.time + ":00");
        const durationMinutes = parseInt(service.duration, 10) || 0;
        const endDate = new Date(startDate.getTime() + durationMinutes * 60000);
        
        return {
          title: "Book service " + service.name,
          start: formatDateForICS(startDate),
          end: formatDateForICS(endDate),
          location: "PIED-DE-POULE, London",
          description: "Service booked with " + service.master
        };
      });
      
      if (isAndroid() && events.length === 1) {
        openGoogleCalendar(events[0]);
      } else {
        downloadICSFileForEvents(events);
      }
      
      setTimeout(() => {
        btnAddCalendar.innerHTML = originalContent;
        btnAddCalendar.disabled = false;
      }, 1000);
    });
  }
}

/************************************
 * Делегированный обработчик для кнопок подтверждения
 ************************************/
document.addEventListener('click', function(e) {
  const btn = e.target.closest('.btn-confirm, .btn-confirm-global, .btn-confirm-cart');
  if (btn) {
    // Если на экране есть confirmation-screen, оно уже сохранено в refreshConfirmationScreen
    const cartItemsContainer = document.getElementById("cartItemsContainer");
    window.savedCartHTML = cartItemsContainer ? cartItemsContainer.innerHTML : "";
    showBookingForm(window.savedCartHTML);
  }
});

/************************************
 * Функция удаления элемента корзины с возвратом слотов мастеру
 ************************************/
function deleteCartItem(serviceId) {
  const index = cartData.findIndex(item => item.id === serviceId);
  if (index !== -1) {
    const serviceInfo = cartData[index];
    if (
      window.freeTimeData &&
      window.freeTimeData[serviceInfo.employeeId] &&
      window.freeTimeData[serviceInfo.employeeId][serviceInfo.date]
    ) {
      const slotsToRestore = generateTimeSlots(serviceInfo.time, serviceInfo.requiredSlots);
      const freeSlots = window.freeTimeData[serviceInfo.employeeId][serviceInfo.date];
      freeSlots.push(...slotsToRestore);
      freeSlots.sort((a, b) => timeToMinutes(a) - timeToMinutes(b));
    }
    releaseSlotOnServer(serviceInfo.employeeId, serviceInfo.date, serviceInfo.time);
    reservedSlotsInfo = reservedSlotsInfo.filter(
      s => !(s.employeeId === serviceInfo.employeeId && s.date === serviceInfo.date && s.time === serviceInfo.time)
    );
    cartData.splice(index, 1);
  }
  renderCart();
  updateCartTotal();

  // Если отображается confirmation-screen, обновляем его содержимое
  const confirmationScreen = document.querySelector(".confirmation-screen");
  if (confirmationScreen) {
    const serviceDetails = confirmationScreen.querySelector(".service-details");
    if (serviceDetails) {
      const cartItemsHTML = document.getElementById("cartItemsContainer").innerHTML;
      serviceDetails.innerHTML = cartItemsHTML;
      refreshConfirmationScreen();
    }
  }

  // Если корзина пуста, возвращаем экран выбора услуг
  if (cartData.length === 0) {
    if (window.innerWidth <= 769) {
      document.querySelector('.leftSide').style.display = "flex";
    }
    document.querySelector('.categoryList').style.display = "flex";
    document.querySelector('.rightSide').style.display = "flex";
    document.querySelector('.booking').style.gridTemplateColumns = "1fr 2fr 1fr";
  }
  
  console.log('Услуга удалена из корзины, слоты возвращены мастеру');
}

window.addEventListener('beforeunload', () => {
  releaseAllReservedSlots();
});