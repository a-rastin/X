import type { Language } from "../../shared/messages";
export const followUpLabels: Record<string, [string, string]> = {
  panss_total: ["Prior PANSS total", "مجموع قبلی PANSS"],
  panss_severity: ["Prior informational severity", "شدت اطلاع‌رسان قبلی"],
  cssrs_severity: [
    "Prior recent ideation severity",
    "شدت افکار اخیر در ویزیت قبلی",
  ],
  cssrs_item_4: ["Prior C-SSRS item 4", "سؤال ۴ قبلی C-SSRS"],
  cssrs_item_5: ["Prior C-SSRS item 5", "سؤال ۵ قبلی C-SSRS"],
  visit_date: ["Source visit date", "تاریخ ویزیت منبع"],
  value: ["Value", "مقدار"],
  field: ["Measurement", "آزمایش"],
  unit: ["Unit", "واحد"],
  select: ["Select an answer", "یک پاسخ انتخاب کنید"],
  bmiMode: ["BMI source", "منبع شاخص توده بدنی"],
  bmiCategory: ["BMI category", "دسته شاخص توده بدنی"],
  visit_ordinal: ["Recorded visit number", "شماره ویزیت ثبت‌شده"],
  sex: ["Sex", "جنس"],
  last_visit_date: ["Last visit date", "تاریخ آخرین ویزیت"],
  comorbidities: ["Recorded comorbidities", "بیماری‌های همراه ثبت‌شده"],
  medication_names: ["Previous medications", "داروهای قبلی"],
  prior_scores: ["Previous assessment results", "نتایج ارزیابی‌های قبلی"],
  prior_labs: [
    "Previous laboratory results and dates",
    "نتایج آزمایش‌های قبلی و تاریخ‌ها",
  ],
  prior_contraception: [
    "Previous contraception recommendations",
    "توصیه‌های قبلی پیشگیری از بارداری",
  ],
  schizophrenia_context: ["Recorded clinical status", "وضعیت بالینی ثبت‌شده"],
  first_time: ["New case", "بیمار جدید"],
  established: ["Known case", "بیمار شناخته‌شده"],
  care_setting: ["Care setting", "محل مراقبت"],
  setting: ["Setting", "محل"],
  reasons: ["Reasons", "دلایل"],
  routine_labs: ["Recommended laboratory tests", "آزمایش‌های پیشنهادی"],
  antidepressant: ["Consider an antidepressant", "در نظر گرفتن ضدافسردگی"],
  imaging: ["Brain MRI", "ام‌آر‌آی مغز"],
  mri: ["MRI indicated", "اندیکاسیون ام‌آر‌آی"],
  do_not_delay_treatment: [
    "Do not delay treatment for imaging",
    "درمان را برای تصویربرداری به تأخیر نیندازید",
  ],
  contraception: ["Contraception counseling", "مشاوره پیشگیری از بارداری"],
  visible: ["Applicable", "قابل اعمال"],
  counsel: [
    "Discuss pregnancy intention and contraception if not desired",
    "گفتگو درباره قصد بارداری و پیشگیری در صورت عدم تمایل",
  ],
  four_weeks: ["Four weeks", "چهار هفته"],
  "2_to_4_weeks": ["Two to four weeks", "دو تا چهار هفته"],
  next_injection: ["At the next injection", "هنگام تزریق بعدی"],
  physician_determines: [
    "Physician determines timing",
    "زمان‌بندی به تشخیص پزشک",
  ],
  cssrs_4: ["C-SSRS recent item 4", "سؤال اخیر ۴ مقیاس C-SSRS"],
  cssrs_5: ["C-SSRS recent item 5", "سؤال اخیر ۵ مقیاس C-SSRS"],
  observation: ["Observation needed", "نیاز به مشاهده"],
  cssrsResult: [
    "Recent ideation severity (highest endorsed item)",
    "شدت افکار اخیر (بالاترین سؤال تأییدشده)",
  ],
  effect_replacement: [
    "Adverse effects take priority over the symptom/adherence pathway",
    "عوارض درمان بر مسیر علائم و پایبندی اولویت دارند",
  ],
  controlled_effect_terminal: [
    "Controlled symptoms with adjunct-treated effects",
    "علائم کنترل‌شده همراه با عوارض قابل درمان کمکی",
  ],
  n4_n5: [
    "Controlled symptoms; medication switch not needed",
    "علائم کنترل‌شده؛ نیازی به تغییر دارو نیست",
  ],
  n4_n8: [
    "Controlled symptoms; physician requests an oral switch",
    "علائم کنترل‌شده؛ پزشک تغییر خوراکی را درخواست می‌کند",
  ],
  n4_n8_n11: [
    "Controlled symptoms; LAI preferred where an equivalent exists",
    "علائم کنترل‌شده؛ در صورت وجود معادل، تزریق طولانی‌اثر ترجیح دارد",
  ],
  n23_n24: [
    "No improvement with treatment resistance",
    "عدم بهبودی همراه با مقاومت به درمان",
  ],
  n25_n11_2: [
    "Nonadherence: same-drug LAI where supported, otherwise oral replacement",
    "عدم پایبندی: فرم تزریقی طولانی‌اثر همان دارو در صورت پشتیبانی، در غیر این صورت جایگزینی خوراکی",
  ],
  n25_n26: [
    "Adherent treatment: persistent harm risk determines the next drug action",
    "درمان با پایبندی: خطر پایدار آسیب، اقدام دارویی بعدی را تعیین می‌کند",
  ],
  actions: ["Care recommendations", "توصیه‌های مراقبتی"],
  age: ["Age", "سن"],
  followUp: ["Follow-up visit", "ویزیت پیگیری"],
  effects: ["Treatment adverse effects", "عوارض درمان"],
  considerations: ["Treatment considerations", "ملاحظات درمان"],
  history: ["History and physical examination", "شرح حال و معاینه جسمی"],
  prior: ["Previous signed visit", "ویزیت امضاشده قبلی"],
  noPrior: ["No prior signed visit", "ویزیت امضاشده قبلی وجود ندارد"],
  details: ["Historical details", "جزئیات سوابق"],
  current: ["Current visit", "ویزیت فعلی"],
  recordedVisits: ["Recorded visit number", "شماره ویزیت ثبت‌شده"],
  unknown: ["Not recorded", "ثبت نشده"],
  main: ["Main antipsychotic (optional)", "داروی ضدروان‌پریشی اصلی (اختیاری)"],
  none: ["None", "هیچ‌کدام"],
  formulation: ["Formulation", "شکل دارویی"],
  oral: ["Oral", "خوراکی"],
  lai: ["Long-acting injection", "تزریق طولانی‌اثر"],
  noMain: [
    "No main oral treatment: physician review; other recommendations still apply.",
    "بدون درمان خوراکی اصلی: بررسی پزشک؛ سایر توصیه‌ها همچنان ارائه می‌شوند.",
  ],
  pending: [
    "Previous medication needs a current catalog selection or explicit removal",
    "داروی قبلی به انتخاب از فهرست فعلی یا حذف صریح نیاز دارد",
  ],
  removed: ["Remove previous item", "حذف داروی قبلی"],
  replacement: [
    "Replace with selected medication",
    "جایگزینی با داروی انتخاب‌شده",
  ],
  recovery: ["Recovery", "بهبودی"],
  controlled: ["Controlled / remission", "کنترل‌شده / فروکش علائم"],
  partial: [
    "Partial improvement without remission",
    "بهبودی نسبی بدون فروکش کامل",
  ],
  none_or_worse: ["No improvement / worse", "بدون بهبودی / بدتر"],
  adherence: ["Adherent to treatment?", "پایبندی به درمان؟"],
  lai_preference: [
    "Prefers LAI for any reason?",
    "تمایل به تزریق طولانی‌اثر به هر دلیل؟",
  ],
  switch_needed: [
    "Need to switch medication for any reason?",
    "نیاز به تغییر دارو به هر دلیل؟",
  ],
  dose_reduction_possible: [
    "Is dose reduction possible?",
    "آیا کاهش دوز ممکن است؟",
  ],
  treatment_resistant: [
    "Are treatment-resistance criteria present?",
    "آیا معیارهای مقاومت به درمان وجود دارد؟",
  ],
  persistent_harm_risk: [
    "Does suicide / other-harm risk persist?",
    "آیا خطر خودکشی یا آسیب به دیگران ادامه دارد؟",
  ],
  dystonia_resolved: [
    "Has acute dystonia resolved after the indicated measures?",
    "آیا دیستونی حاد پس از اقدامات لازم برطرف شده است؟",
  ],
  sedation: ["Sedation", "خواب‌آلودگی"],
  tolerable: ["Yes, tolerable", "بله، قابل تحمل"],
  intervention: ["Yes, requires intervention", "بله، نیازمند مداخله"],
  orthostatic_hypotension: ["Orthostatic hypotension", "افت فشار خون وضعیتی"],
  akathisia: ["Akathisia", "آکاتیزیا"],
  parkinsonism: ["Parkinsonism", "پارکینسونیسم"],
  acute_dystonia: ["Acute dystonia", "دیستونی حاد"],
  tardive_dyskinesia: ["Tardive dyskinesia", "دیسکینزی دیررس"],
  anticholinergic: ["Anticholinergic effects", "عوارض آنتی‌کولینرژیک"],
  hospitalization_count: [
    "Prior hospitalization count (optional)",
    "تعداد بستری‌های قبلی (اختیاری)",
  ],
  current_antidepressant: [
    "Currently taking an antidepressant?",
    "آیا اکنون داروی ضدافسردگی مصرف می‌کند؟",
  ],
  opioids: ["Opioid derivatives", "مشتقات اپیوئید"],
  methamphetamine: ["Methamphetamine", "مت‌آمفتامین"],
  pregnant: ["Pregnant?", "باردار؟"],
  hyperprolactinemia: ["Hyperprolactinemia", "هیپرپرولاکتینمی"],
  requiredInputs: ["Complete the required field", "فیلد الزامی را تکمیل کنید"],
  physician_review: [
    "Physician review: no automated antipsychotic change",
    "بررسی پزشک: بدون تغییر خودکار داروی ضدروان‌پریشی",
  ],
  continue_same: [
    "Continue the same drug at the same dose",
    "ادامه همان دارو با همان دوز",
  ],
  reduce_dose: [
    "Continue the same drug at a lower dose",
    "ادامه همان دارو با دوز کمتر",
  ],
  adjunct_only: [
    "No antipsychotic change; adjunct advice below",
    "بدون تغییر ضدروان‌پریشی؛ توصیه درمان کمکی در زیر",
  ],
  oral_replacement: [
    "Proposed oral replacements",
    "جایگزین‌های خوراکی پیشنهادی",
  ],
  same_drug_lai: [
    "Same-drug long-acting injection",
    "فرم تزریقی طولانی‌اثر همان دارو",
  ],
  clozapine: ["Clozapine", "کلوزاپین"],
  vmat2_inhibitor: ["VMAT2-inhibitor advice", "توصیه مهارکننده VMAT2"],
  reduce_dose_if_possible: [
    "Reduce dose if possible",
    "در صورت امکان کاهش دوز",
  ],
  anticholinergic_advice: [
    "Anticholinergic adjunct advice",
    "توصیه درمان کمکی آنتی‌کولینرژیک",
  ],
  adjuncts: ["Adjunct advice", "توصیه‌های درمان کمکی"],
  exclusions: [
    "Candidate exclusions and source warnings",
    "موارد حذف داروها و هشدارهای منابع",
  ],
  trace: ["Decision trace", "مسیر تصمیم"],
  current_interactions: [
    "Current medication interactions",
    "تداخلات داروهای فعلی",
  ],
  candidate_interactions: [
    "Candidate medication interactions",
    "تداخلات داروهای پیشنهادی",
  ],
  no_main: [
    "No main antipsychotic selected",
    "داروی ضدروان‌پریشی اصلی انتخاب نشده",
  ],
  current_lai: [
    "Current treatment is a long-acting injection",
    "درمان فعلی تزریق طولانی‌اثر است",
  ],
  no_eligible_oral_candidate: [
    "No eligible oral candidate remains",
    "هیچ داروی خوراکی واجد شرایط باقی نمانده",
  ],
  direct_excluded: [
    "Direct recommendation excluded by availability or D/X interaction",
    "توصیه مستقیم به علت عدم دسترسی یا تداخل D/X حذف شد",
  ],
  unsupported_direct_mapping: [
    "Direct formulation mapping unavailable",
    "نگاشت شکل دارویی مستقیم موجود نیست",
  ],
  unavailable: ["Unavailable", "غیرقابل دسترس"],
  current_main: ["Current main drug", "داروی اصلی فعلی"],
  zero_probability: ["Zero model share", "سهم صفر مدل"],
  interaction_dx: ["D/X interaction", "تداخل D/X"],
};

export function followUpMessage(
  language: Language,
  key: string,
  catalog: Record<string, string>,
): string {
  const normalized = key.replaceAll(" ", "_").toLowerCase();
  const label = followUpLabels[key] ?? followUpLabels[normalized];
  if (label) return label[language === "fa" ? 1 : 0];
  const resolved = Object.hasOwn(catalog, `nc.${key}`) ? key : normalized;
  return catalog[`nc.${resolved}`] ?? key.replaceAll("_", " ");
}
