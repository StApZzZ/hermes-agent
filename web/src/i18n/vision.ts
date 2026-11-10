// Strings of the Images page (pages/VisionPage.tsx). A separate module so the large locale files
// only gain one line each; locales without a translation fall back to English.

export interface VisionStrings {
  title: string;
  subtitle: string;
  modeTitle: string;
  modeHint: string;
  modes: Record<"auto" | "native" | "text", { label: string; description: string }>;
  saved: string;
  saveFailed: string;
  mainModel: string;
  describer: string;
  noDescriber: string;
  routeNow: string;
  routeNative: string;
  routeText: string;
  changeDescriber: string;
  toolsTitle: string;
  toolsHint: string;
  installed: string;
  missing: string;
  languages: string;
  refresh: string;
  toolPurpose: Record<string, string>;
  tryTitle: string;
  tryHint: string;
  dropHere: string;
  pickFile: string;
  question: string;
  questionPlaceholder: string;
  /** Sent when the question is empty, so the description comes back in the UI language. */
  defaultQuestion: string;
  ocrLanguages: string;
  actions: Record<"describe" | "ocr" | "codes" | "meta", string>;
  run: string;
  running: string;
  uploadFailed: string;
  noText: string;
  noCodes: string;
  noExif: string;
  openMap: string;
  notInstalled: string;
  chatHint: string;
  draw: {
    title: string;
    hint: string;
    backend: string;
    noBackend: string;
    prompt: string;
    placeholder: string;
    aspects: Record<"landscape" | "square" | "portrait", string>;
    run: string;
    running: string;
    download: string;
    chatHint: string;
  };
}

export const visionEn: VisionStrings = {
  title: "Image recognition",
  subtitle: "How Hermes looks at pictures you send in chats, which tools it has, a place to try them, and drawing new pictures.",
  modeTitle: "How images reach the model",
  modeHint: "Applies from the next picture, in every chat. Same switch as /settings → Recognize images in Telegram.",
  modes: {
    auto: {
      label: "Auto",
      description: "The model sees the picture if it can; otherwise it gets a text description.",
    },
    native: {
      label: "Native",
      description: "Always send the picture itself to the model. Best quality when the model has vision.",
    },
    text: {
      label: "Description",
      description: "Always describe the picture first (vision_analyze) and send the text. Works with any model.",
    },
  },
  saved: "Saved",
  saveFailed: "Could not save",
  mainModel: "Main model",
  describer: "Describes images",
  noDescriber: "No backend can describe images right now. Pick a vision model on the Models page.",
  routeNow: "A picture sent now goes",
  routeNative: "straight to the model",
  routeText: "as a description",
  changeDescriber: "Change the describing model",
  toolsTitle: "Recognition tools on the server",
  toolsHint: "The image-recognition skill uses them for exact text, codes and photo data.",
  installed: "installed",
  missing: "missing",
  languages: "Languages",
  refresh: "Refresh",
  toolPurpose: {
    tesseract: "Text from photos and screenshots (OCR)",
    zbarimg: "QR codes and barcodes",
    exiftool: "Camera, time and GPS from photos",
    claude: "Description through the Claude subscription",
    codex: "Description through the ChatGPT subscription",
  },
  tryTitle: "Try it",
  tryHint: "Drop, paste or pick a picture, choose what to find, and run.",
  dropHere: "Drop an image here or paste it (Ctrl+V)",
  pickFile: "Choose file",
  question: "Question (optional)",
  questionPlaceholder: "What is in the picture? What does the chart show?",
  defaultQuestion: "Describe the picture precisely and copy any visible text word for word.",
  ocrLanguages: "OCR languages",
  actions: { describe: "Description", ocr: "Text (OCR)", codes: "QR / barcodes", meta: "Photo data (EXIF)" },
  run: "Recognise",
  running: "Recognising…",
  uploadFailed: "Upload failed",
  noText: "No text found.",
  noCodes: "No codes found.",
  noExif: "No EXIF data (messengers and screenshots usually strip it).",
  openMap: "Open on the map",
  notInstalled: "not installed",
  chatHint: "In chats: send a photo, or ask “read the text on the picture” — the agent uses these tools itself.",
  draw: {
    title: "Draw a picture",
    hint: "Describe the picture; the chosen backend draws it (Codex CLI uses your ChatGPT subscription, about 30–60 s).",
    backend: "Who draws",
    noBackend: "No backend can draw here yet. Install Codex CLI and sign in (codex login), then refresh.",
    prompt: "What to draw",
    placeholder: "A cosy reading nook by a rainy window, watercolor",
    aspects: { landscape: "Landscape", square: "Square", portrait: "Portrait" },
    run: "Draw",
    running: "Drawing…",
    download: "Download",
    chatHint: "In chats: ask “draw …” — the agent uses the same backend (from a new conversation after a switch).",
  },
};

export const visionRu: VisionStrings = {
  title: "Распознавание изображений",
  subtitle: "Как Hermes смотрит на картинки из чатов, какие инструменты у него есть, где их попробовать — и как нарисовать новую картинку.",
  modeTitle: "Как картинки попадают к модели",
  modeHint: "Действует со следующей картинки во всех чатах. То же, что /settings → Распознавание в Telegram.",
  modes: {
    auto: {
      label: "Авто",
      description: "Модель видит картинку, если умеет; иначе получает текстовое описание.",
    },
    native: {
      label: "Напрямую",
      description: "Всегда отправлять модели саму картинку. Лучшее качество, если модель видит изображения.",
    },
    text: {
      label: "Описание",
      description: "Всегда сначала описывать картинку (vision_analyze) и передавать текст. Работает с любой моделью.",
    },
  },
  saved: "Сохранено",
  saveFailed: "Не удалось сохранить",
  mainModel: "Основная модель",
  describer: "Описывает картинки",
  noDescriber: "Сейчас картинки описывать некому. Выберите модель со зрением на странице «Модели».",
  routeNow: "Картинка, отправленная сейчас, пойдёт",
  routeNative: "сразу модели",
  routeText: "в виде описания",
  changeDescriber: "Сменить модель для описания",
  toolsTitle: "Инструменты распознавания на сервере",
  toolsHint: "Навык image-recognition использует их для точного текста, кодов и данных фото.",
  installed: "установлен",
  missing: "нет",
  languages: "Языки",
  refresh: "Обновить",
  toolPurpose: {
    tesseract: "Текст с фото и скриншотов (OCR)",
    zbarimg: "QR-коды и штрихкоды",
    exiftool: "Камера, время и GPS из фото",
    claude: "Описание через подписку Claude",
    codex: "Описание через подписку ChatGPT",
  },
  tryTitle: "Попробовать",
  tryHint: "Перетащите, вставьте или выберите картинку, отметьте, что искать, и запустите.",
  dropHere: "Перетащите картинку сюда или вставьте её (Ctrl+V)",
  pickFile: "Выбрать файл",
  question: "Вопрос (необязательно)",
  questionPlaceholder: "Что на картинке? Что показывает график?",
  defaultQuestion: "Опиши картинку точно, по-русски, и перепиши весь видимый текст дословно.",
  ocrLanguages: "Языки OCR",
  actions: { describe: "Описание", ocr: "Текст (OCR)", codes: "QR / штрихкоды", meta: "Данные фото (EXIF)" },
  run: "Распознать",
  running: "Распознаю…",
  uploadFailed: "Не удалось загрузить",
  noText: "Текст не найден.",
  noCodes: "Коды не найдены.",
  noExif: "Нет EXIF (мессенджеры и скриншоты обычно его удаляют).",
  openMap: "Открыть на карте",
  notInstalled: "не установлен",
  chatHint: "В чатах: просто пришлите фото или попросите «прочитай текст на картинке» — агент сам воспользуется этими инструментами.",
  draw: {
    title: "Нарисовать картинку",
    hint: "Опишите картинку — её нарисует выбранный способ (Codex CLI рисует по подписке ChatGPT, примерно 30–60 с).",
    backend: "Кто рисует",
    noBackend: "Рисовать пока некому. Установите Codex CLI и войдите (codex login), затем обновите страницу.",
    prompt: "Что нарисовать",
    placeholder: "Уютный уголок для чтения у окна в дождь, акварель",
    aspects: { landscape: "Горизонтально", square: "Квадрат", portrait: "Вертикально" },
    run: "Нарисовать",
    running: "Рисую…",
    download: "Скачать",
    chatHint: "В чатах: попросите «нарисуй …» — агент рисует тем же способом (после смены — с нового разговора).",
  },
};
