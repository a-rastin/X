import React, { useState, useEffect, createContext, useContext } from "react";
import { createRoot } from "react-dom/client";
import { Theme } from "@astryxdesign/core/theme";
import { AppShell } from "@astryxdesign/core/AppShell";
import {
  SideNav,
  SideNavHeading,
  SideNavItem,
  SideNavSection,
} from "@astryxdesign/core/SideNav";
import { Layout, LayoutContent } from "@astryxdesign/core/Layout";
import { VStack, HStack, StackItem } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { Heading } from "@astryxdesign/core/Heading";
import { Button } from "@astryxdesign/core/Button";
import { TextInput } from "@astryxdesign/core/TextInput";
import { TextArea } from "@astryxdesign/core/TextArea";
import { CheckboxInput } from "@astryxdesign/core/CheckboxInput";
import { Section } from "@astryxdesign/core/Section";
import { Divider } from "@astryxdesign/core/Divider";
import { Table } from "@astryxdesign/core/Table";
import { academicTheme as academic } from "./built/academic.js";
import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
import "./built/academic.css";

const Language = createContext(false);
const useT = () => {
  const fa = useContext(Language);
  return (en: string, faText: string) => (fa ? faText : en);
};
type View =
  | "patients"
  | "assessment"
  | "medications"
  | "proposal"
  | "chart"
  | "admin"
  | "login";
const views: [View, string, string][] = [
  ["patients", "Patients", "بیماران"],
  ["assessment", "PANSS assessment", "ارزیابی PANSS"],
  ["medications", "Medications", "داروها"],
  ["proposal", "Proposal review", "مرور پیشنهاد"],
  ["chart", "Signed chart", "پرونده امضاشده"],
  ["admin", "Administration", "مدیریت"],
  ["login", "Sign in", "ورود"],
];
function Muted({ children }: { children: React.ReactNode }) {
  return (
    <Text type="supporting" color="secondary">
      {children}
    </Text>
  );
}
function State({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return (
    <Text className={"state " + tone} type="supporting">
      {children}
    </Text>
  );
}
function Title({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <HStack
      className="title-row"
      hAlign="between"
      vAlign="center"
      gap={4}
      wrap="wrap"
    >
      <VStack gap={2}>
        <Heading level={1}>{title}</Heading>
        <Muted>{description}</Muted>
      </VStack>
      {action}
    </HStack>
  );
}
function Notice({
  children,
  warning = false,
}: {
  children: React.ReactNode;
  warning?: boolean;
}) {
  return (
    <HStack
      className={warning ? "notice warning" : "notice"}
      gap={3}
      vAlign="start"
    >
      <Text weight="bold">{warning ? "!" : "i"}</Text>
      <Text type="supporting">{children}</Text>
    </HStack>
  );
}
function ReviewLabel() {
  const t = useT();
  return (
    <Muted>
      {t(
        "Prototype only. All records and clinical values are illustrative.",
        "فقط نمونه طراحی. همه سوابق و مقادیر بالینی ساختگی هستند.",
      )}
    </Muted>
  );
}

const samplePatients = [
  {
    id: "DEMO-001",
    en: "Demo patient 01",
    fa: "بیمار نمونه ۰۱",
    age: 28,
    sex: "Female",
    status: "New case",
    archived: false,
  },
  {
    id: "DEMO-002",
    en: "Demo patient 02",
    fa: "بیمار نمونه ۰۲",
    age: 36,
    sex: "Male",
    status: "Known case",
    archived: false,
  },
  {
    id: "DEMO-003",
    en: "Demo patient 03",
    fa: "بیمار نمونه ۰۳",
    age: 42,
    sex: "Female",
    status: "Known case",
    archived: false,
  },
  {
    id: "DEMO-004",
    en: "Demo patient 04",
    fa: "بیمار نمونه ۰۴",
    age: 31,
    sex: "Male",
    status: "New case",
    archived: false,
  },
  {
    id: "DEMO-005",
    en: "Demo patient 05",
    fa: "بیمار نمونه ۰۵",
    age: 54,
    sex: "Female",
    status: "Known case",
    archived: true,
  },
  {
    id: "DEMO-006",
    en: "Demo patient 06",
    fa: "بیمار نمونه ۰۶",
    age: 24,
    sex: "Male",
    status: "Known case",
    archived: false,
  },
];
function Patients({ go }: { go: (v: View) => void }) {
  const t = useT(),
    fa = useContext(Language);
  const [q, setQ] = useState(""),
    [filter, setFilter] = useState("all"),
    [state, setState] = useState("ready");
  const data = samplePatients.filter(
    (p) =>
      (p.en + p.fa + p.id).toLowerCase().includes(q.toLowerCase()) &&
      (filter === "all" || p.status === filter),
  );
  return (
    <VStack gap={6}>
      <Title
        title={t("Patients", "بیماران")}
        description={t(
          "Shared patient directory for the physician team.",
          "فهرست مشترک بیماران تیم پزشکان.",
        )}
        action={
          <Button
            label={t("Register patient", "ثبت بیمار")}
            variant="primary"
            onClick={() => go("assessment")}
          />
        }
      />
      <HStack className="filters" gap={4} vAlign="end" wrap="wrap">
        <TextInput
          label={t(
            "Search by name or patient ID",
            "جستجو با نام یا شناسه بیمار",
          )}
          placeholder={t("Search patients…", "جستجوی بیماران…")}
          value={q}
          onChange={setQ}
          width="var(--xi-field-width)"
        />
        <VStack gap={2} as="label">
          <Text type="label">{t("Clinical status", "وضعیت بالینی")}</Text>
          <select
            aria-label={t("Clinical status", "وضعیت بالینی")}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="all">{t("All statuses", "همه وضعیت‌ها")}</option>
            <option value="New case">{t("New case", "مورد جدید")}</option>
            <option value="Known case">
              {t("Known case", "مورد شناخته‌شده")}
            </option>
          </select>
        </VStack>
      </HStack>
      {state === "error" ? (
        <Notice warning>
          <Text type="inherit">
            {t(
              "Patient directory could not be loaded. Check the connection and retry.",
              "بارگذاری فهرست ناموفق بود. اتصال را بررسی و دوباره تلاش کنید.",
            )}
          </Text>
          <Button
            label={t("Retry", "تلاش مجدد")}
            size="sm"
            onClick={() => setState("ready")}
          />
        </Notice>
      ) : state === "loading" ? (
        <Text role="status">
          {t("Loading patient directory…", "در حال بارگذاری فهرست بیماران…")}
        </Text>
      ) : data.length === 0 ? (
        <VStack className="empty" gap={3}>
          <Heading level={2}>
            {t("No matching patients", "بیماری پیدا نشد")}
          </Heading>
          <Muted>
            {t(
              "Try a different name, patient ID, or clinical status.",
              "نام، شناسه یا وضعیت دیگری را امتحان کنید.",
            )}
          </Muted>
          <Button
            label={t("Clear search", "پاک کردن جستجو")}
            onClick={() => {
              setQ("");
              setFilter("all");
            }}
          />
        </VStack>
      ) : (
        <Section padding={0} className="table-frame">
          <Table
            aria-label={t("Patient directory", "فهرست بیماران")}
            data={data}
            columns={[
              {
                key: "en",
                header: t("Patient", "بیمار"),
                renderCell: (p) => (
                  <VStack gap={1}>
                    <Text weight="semibold">{fa ? p.fa : p.en}</Text>
                    <Muted>
                      <bdi dir="ltr">{p.id}</bdi>
                    </Muted>
                  </VStack>
                ),
              },
              {
                key: "age",
                header: t("Age / sex", "سن / جنس"),
                renderCell: (p) => (
                  <Text>
                    {p.age} /{" "}
                    {p.sex === "Female" ? t("Female", "زن") : t("Male", "مرد")}
                  </Text>
                ),
              },
              {
                key: "status",
                header: t("Clinical status", "وضعیت بالینی"),
                renderCell: (p) => (
                  <State tone={p.status === "New case" ? "teal" : "neutral"}>
                    {p.status === "New case"
                      ? t("New case", "مورد جدید")
                      : t("Known case", "مورد شناخته‌شده")}
                  </State>
                ),
              },
              {
                key: "archived",
                header: t("Record", "پرونده"),
                renderCell: (p) => (
                  <Muted>
                    {p.archived
                      ? t("Archived", "بایگانی‌شده")
                      : t("Active", "فعال")}
                  </Muted>
                ),
              },
              {
                key: "id",
                header: t("Actions", "اقدامات"),
                renderCell: (p) => (
                  <HStack gap={1} wrap="wrap">
                    <Button
                      label={t("View chart", "مشاهده پرونده")}
                      variant="ghost"
                      size="sm"
                      onClick={() => go("chart")}
                    />
                    {!p.archived && (
                      <Button
                        label={t("Follow-up", "پیگیری")}
                        variant="secondary"
                        size="sm"
                        onClick={() => go("assessment")}
                      />
                    )}
                  </HStack>
                ),
              },
            ]}
          />
          <HStack
            className="table-footer"
            padding={4}
            hAlign="between"
            vAlign="center"
            wrap="wrap"
            gap={3}
          >
            <Muted>
              {t(
                `Showing ${data.length} of ${data.length} patients`,
                `نمایش ${data.length} بیمار از ${data.length}`,
              )}
            </Muted>
            <HStack gap={2}>
              <Button label={t("Previous", "قبلی")} size="sm" isDisabled />
              <Button label={t("Next", "بعدی")} size="sm" isDisabled />
            </HStack>
          </HStack>
        </Section>
      )}
      <Notice>
        {t(
          "Research prototype. Not clinically validated. Clinical decisions remain the responsibility of the treating physician.",
          "نمونه پژوهشی؛ از نظر بالینی اعتبارسنجی نشده است. تصمیم‌گیری بالینی بر عهده پزشک معالج است.",
        )}
      </Notice>
      <details className="design-details">
        <summary>
          {t(
            "Preview loading and error states",
            "پیش‌نمایش حالت‌های بارگذاری و خطا",
          )}
        </summary>
        <HStack gap={2} paddingBlock={3} wrap="wrap">
          {["ready", "loading", "error"].map((s) => (
            <Button
              key={s}
              label={
                s === "ready"
                  ? t("Loaded", "بارگذاری‌شده")
                  : s === "loading"
                    ? t("Loading", "در حال بارگذاری")
                    : t("Connection error", "خطای اتصال")
              }
              size="sm"
              onClick={() => setState(s)}
            />
          ))}
        </HStack>
      </details>
    </VStack>
  );
}
const steps = [
  ["Diagnosis", "تشخیص"],
  ["PANSS", "PANSS"],
  ["Harm assessment", "ارزیابی آسیب"],
  ["History", "شرح حال"],
  ["Medications", "داروها"],
  ["Considerations", "ملاحظات"],
  ["Proposal", "پیشنهاد"],
];
function EncounterHeader({
  active,
  go,
}: {
  active: number;
  go: (v: View) => void;
}) {
  const t = useT();
  return (
    <VStack gap={4}>
      <HStack hAlign="between" vAlign="center" gap={3} wrap="wrap">
        <HStack gap={3} vAlign="center" wrap="wrap">
          <Text weight="semibold">
            {t("Demo patient 01", "بیمار نمونه ۰۱")}
          </Text>
          <Muted>
            <bdi dir="ltr">DEMO-001</bdi>
          </Muted>
          <State tone="teal">{t("New case", "مورد جدید")}</State>
        </HStack>
        <State>
          {t("Draft · illustrative state", "پیش‌نویس · وضعیت نمونه")}
        </State>
      </HStack>
      <HStack
        className="step-track"
        gap={0}
        as="nav"
        aria-label={t("New case steps", "مراحل مورد جدید")}
      >
        {steps.map((s, i) => (
          <button
            key={i}
            className={i === active ? "step active" : "step"}
            aria-current={i === active ? "step" : undefined}
            onClick={() =>
              go(i === 4 ? "medications" : i === 6 ? "proposal" : "assessment")
            }
          >
            <Text type="supporting" className="step-number">
              {i + 1}
            </Text>
            <Text type="supporting">{t(s[0], s[1])}</Text>
          </button>
        ))}
      </HStack>
    </VStack>
  );
}
const questions = [
  ["P1", "Delusions", "هذیان"],
  ["P2", "Conceptual disorganization", "آشفتگی مفهومی"],
  ["P3", "Hallucinatory behavior", "رفتار توهمی"],
  ["P4", "Excitement", "برانگیختگی"],
  ["P5", "Grandiosity", "بزرگ‌منشی"],
  ["P6", "Suspiciousness / persecution", "بدگمانی / گزند و آسیب"],
  ["P7", "Hostility", "خصومت"],
];
const ratingNames = [
  ["Absent", "وجود ندارد"],
  ["Minimal", "حداقل"],
  ["Mild", "خفیف"],
  ["Moderate", "متوسط"],
  ["Moderately severe", "نسبتاً شدید"],
  ["Severe", "شدید"],
  ["Extreme", "بسیار شدید"],
];
function Assessment({ go }: { go: (v: View) => void }) {
  const t = useT();
  const [ratings, setRatings] = useState<Record<string, string>>({
      P1: "3",
      P2: "2",
      P3: "4",
    }),
    [sub, setSub] = useState(0);
  const subs = [
    ["Positive symptoms", "علائم مثبت", 7],
    ["Negative symptoms", "علائم منفی", 7],
    ["General psychopathology", "آسیب‌شناسی عمومی روانی", 16],
  ];
  const items =
    sub === 0
      ? questions
      : sub === 1
        ? [
            ["N1", "Blunted affect", "کندی عاطفه"],
            ["N2", "Emotional withdrawal", "کناره‌گیری عاطفی"],
            ["N3", "Poor rapport", "ارتباط ضعیف"],
            [
              "N4",
              "Passive / apathetic social withdrawal",
              "کناره‌گیری اجتماعی منفعلانه",
            ],
            ["N5", "Difficulty in abstract thinking", "دشواری در تفکر انتزاعی"],
            [
              "N6",
              "Lack of spontaneity and flow of conversation",
              "فقدان خودانگیختگی در گفتار",
            ],
            ["N7", "Stereotyped thinking", "تفکر قالبی"],
          ]
        : [
            ["G1", "Somatic concern", "نگرانی جسمی"],
            ["G2", "Anxiety", "اضطراب"],
            ["G3", "Guilt feelings", "احساس گناه"],
            ["G4", "Tension", "تنش"],
          ];
  return (
    <VStack gap={6}>
      <EncounterHeader active={1} go={go} />
      <Title
        title={t("PANSS assessment", "ارزیابی PANSS")}
        description={t(
          "Positive and Negative Syndrome Scale · 30 items",
          "مقیاس علائم مثبت و منفی · ۳۰ گویه",
        )}
      />
      <HStack className="assessment-layout" gap={6} vAlign="start">
        <VStack className="assessment-main" gap={4}>
          <HStack className="subscale-tabs" gap={1} wrap="wrap">
            {subs.map((s, i) => (
              <Button
                key={i}
                label={t(String(s[0]), String(s[1]))}
                size="sm"
                variant={sub === i ? "primary" : "ghost"}
                onClick={() => setSub(i)}
              />
            ))}
          </HStack>
          <HStack hAlign="between" gap={3} wrap="wrap">
            <Heading level={2}>
              {t(String(subs[sub][0]), String(subs[sub][1]))}
            </Heading>
            <Muted>
              {t(
                "Select one rating per item.",
                "برای هر گویه یک امتیاز انتخاب کنید.",
              )}
            </Muted>
          </HStack>
          <VStack gap={0} className="worksheet">
            {items.map(([id, en, fa]) => (
              <fieldset key={id} className="rating-row">
                <legend>
                  <Text type="supporting" color="secondary">
                    {id}
                  </Text>{" "}
                  <Text weight="semibold">{t(en, fa)}</Text>
                </legend>
                <HStack className="rating-options" gap={2}>
                  {ratingNames.map(([en, fa], i) => (
                    <label key={i} title={t(en, fa)}>
                      <input
                        type="radio"
                        name={id}
                        value={i + 1}
                        checked={ratings[id] === String(i + 1)}
                        onChange={(e) =>
                          setRatings({ ...ratings, [id]: e.target.value })
                        }
                      />
                      <Text>{i + 1}</Text>
                    </label>
                  ))}
                </HStack>
                <Muted>
                  {ratings[id]
                    ? t(
                        ratingNames[Number(ratings[id]) - 1][0],
                        ratingNames[Number(ratings[id]) - 1][1],
                      )
                    : t("Not answered", "بدون پاسخ")}
                </Muted>
              </fieldset>
            ))}
          </VStack>
          {sub === 2 && (
            <Muted>
              {t(
                "First four general items shown in this visual preview. The app retains all 16.",
                "چهار گویه اول در پیش‌نمایش نمایش داده شده‌اند. برنامه هر ۱۶ گویه را حفظ می‌کند.",
              )}
            </Muted>
          )}
          <details>
            <summary>{t("Rating reference", "راهنمای امتیازدهی")}</summary>
            <VStack paddingBlock={3} gap={2}>
              {ratingNames.map(([en, fa], i) => (
                <Text key={i} type="supporting">
                  {i + 1}. {t(en, fa)}
                </Text>
              ))}
            </VStack>
          </details>
        </VStack>
        <VStack className="results-rail" gap={4} padding={5}>
          <Heading level={2}>
            {t("Assessment summary", "خلاصه ارزیابی")}
          </Heading>
          <State tone="warning">{t("Incomplete", "ناقص")}</State>
          <Muted>
            {t(
              "An unanswered item is not a zero. Scores appear after the complete assessment is saved and evaluated.",
              "گویه بدون پاسخ به معنای صفر نیست. امتیازها پس از ذخیره و ارزیابی کامل نمایش داده می‌شوند.",
            )}
          </Muted>
          <Divider />
          {subs.map((s, i) => (
            <HStack key={i} hAlign="between" gap={3}>
              <Text type="supporting">{t(String(s[0]), String(s[1]))}</Text>
              <Text>—</Text>
            </HStack>
          ))}
          <Divider />
          <HStack hAlign="between">
            <Text weight="semibold">{t("Total score", "امتیاز کل")}</Text>
            <Text weight="bold">— / 210</Text>
          </HStack>
          <Muted>
            {t(
              "Prototype ratings are local and never saved. No clinical score is calculated here.",
              "امتیازهای نمونه محلی‌اند و ذخیره نمی‌شوند. امتیاز بالینی در اینجا محاسبه نمی‌شود.",
            )}
          </Muted>
        </VStack>
      </HStack>
      <HStack className="action-bar" hAlign="between" gap={3} wrap="wrap">
        <Button label={t("Back", "بازگشت")} onClick={() => go("patients")} />
        <Muted>{t("Step 2 of 7", "مرحله ۲ از ۷")}</Muted>
        <Button
          label={t(
            "Continue · preview medications",
            "ادامه · پیش‌نمایش داروها",
          )}
          variant="primary"
          onClick={() => go("medications")}
        />
      </HStack>
    </VStack>
  );
}
function Medications({ go }: { go: (v: View) => void }) {
  const t = useT();
  const [q, setQ] = useState(""),
    [drugs, setDrugs] = useState(["Metformin"]);
  return (
    <VStack gap={6}>
      <EncounterHeader active={4} go={go} />
      <Title
        title={t("Current medications", "داروهای فعلی")}
        description={t(
          "Review the medication list and available interaction evidence.",
          "فهرست داروها و شواهد تداخل موجود را مرور کنید.",
        )}
      />
      <HStack className="two-column" gap={6} vAlign="start">
        <VStack className="column" gap={5}>
          <TextInput
            label={t("Find a medication", "جستجوی دارو")}
            value={q}
            onChange={setQ}
            placeholder={t(
              "Search the medication catalogue…",
              "جستجو در فهرست دارویی…",
            )}
          />
          {q && (
            <VStack className="search-results" padding={3} gap={2}>
              {["Metformin", "Amlodipine", "Sertraline"]
                .filter((n) => n.toLowerCase().includes(q.toLowerCase()))
                .map((n) => (
                  <Button
                    label={t(`Add ${n}`, `افزودن ${n}`)}
                    key={n}
                    onClick={() => {
                      setDrugs([...new Set([...drugs, n])]);
                      setQ("");
                    }}
                  />
                ))}
            </VStack>
          )}
          <HStack hAlign="between">
            <Heading level={2}>
              {t("Selected medications", "داروهای انتخاب‌شده")}
            </Heading>
            <Muted>{drugs.length}</Muted>
          </HStack>
          <VStack gap={0} className="medication-list">
            {drugs.map((n) => (
              <HStack
                key={n}
                className="medication-row"
                paddingBlock={4}
                hAlign="between"
                vAlign="center"
              >
                <Text weight="semibold">
                  <bdi dir="ltr">{n}</bdi>
                </Text>
                <Button
                  label={t(`Remove ${n}`, `حذف ${n}`)}
                  variant="ghost"
                  size="sm"
                  onClick={() => setDrugs(drugs.filter((d) => d !== n))}
                />
              </HStack>
            ))}
          </VStack>
          <Notice>
            {t(
              "Only catalogue selections are included. An unmatched search term is not a medication entry.",
              "فقط داروهای انتخاب‌شده از فهرست ثبت می‌شوند. عبارت جستجوی نامنطبق داروی ثبت‌شده نیست.",
            )}
          </Notice>
        </VStack>
        <VStack className="column evidence" gap={5} padding={5}>
          <HStack hAlign="between" vAlign="center" gap={3}>
            <Heading level={2}>
              {t("Interaction review", "مرور تداخلات")}
            </Heading>
            <State>{t("Lexicomp", "لکسی‌کامپ")}</State>
          </HStack>
          <Muted>
            {t(
              "New case uses its pinned Lexicomp dataset. Known case and follow-up retain DDInter.",
              "مورد جدید از نسخه ثابت داده‌های لکسی‌کامپ استفاده می‌کند. مسیر قبلی DDInter حفظ می‌شود.",
            )}
          </Muted>
          <Divider />
          <Heading level={3}>
            {t("Candidate interaction evidence", "شواهد تداخل داروهای کاندید")}
          </Heading>
          <Notice warning>
            {t(
              "Evidence unavailable in this sample. No interaction result does not establish safety.",
              "شواهد در این نمونه موجود نیست. نبود نتیجه تداخل به معنای ایمنی نیست.",
            )}
          </Notice>
          <details>
            <summary>
              {t("Coverage and source details", "جزئیات پوشش و منبع")}
            </summary>
            <Text type="supporting">
              {t(
                "The production view retains risk ratings, source text, coverage gaps, and the versioned dataset. No interaction check is executed in this prototype.",
                "نمای واقعی رتبه خطر، متن منبع، شکاف‌های پوشش و نسخه داده را حفظ می‌کند. در این نمونه بررسی تداخل اجرا نمی‌شود.",
              )}
            </Text>
          </details>
        </VStack>
      </HStack>
      <HStack className="action-bar" hAlign="between" wrap="wrap" gap={3}>
        <Button label={t("Back", "بازگشت")} onClick={() => go("assessment")} />
        <Muted>{t("Step 5 of 7", "مرحله ۵ از ۷")}</Muted>
        <Button
          label={t("Preview proposal", "پیش‌نمایش پیشنهاد")}
          variant="primary"
          onClick={() => go("proposal")}
        />
      </HStack>
    </VStack>
  );
}
function Proposal({ go }: { go: (v: View) => void }) {
  const t = useT(),
    [plan, setPlan] = useState(""),
    [ack, setAck] = useState(false),
    [msg, setMsg] = useState("");
  return (
    <VStack gap={6}>
      <EncounterHeader active={6} go={go} />
      <Title
        title={t("Proposal review", "مرور پیشنهاد")}
        description={t(
          "Review the original proposal, then prepare your final plan.",
          "پیشنهاد اصلی را مرور و سپس برنامه نهایی خود را تهیه کنید.",
        )}
      />
      <Notice warning>
        {t(
          "Illustrative proposal layout. No treatment recommendation or model result has been generated.",
          "چیدمان نمونه پیشنهاد. هیچ توصیه درمانی یا نتیجه مدلی تولید نشده است.",
        )}
      </Notice>
      <HStack className="two-column" gap={6} vAlign="start">
        <VStack className="column proposal-original" gap={5} padding={5}>
          <HStack hAlign="between" gap={3} wrap="wrap">
            <Heading level={2}>
              {t("Original system proposal", "پیشنهاد اصلی سیستم")}
            </Heading>
            <State>{t("Read only", "فقط خواندنی")}</State>
          </HStack>
          <Muted>
            {t("New case · fixed Bayesian model", "مورد جدید · مدل بیزی ثابت")}
          </Muted>
          <Divider />
          {[
            [
              "Clinical considerations",
              "ملاحظات بالینی",
              "Structured recommendations and their recorded reasons appear here.",
              "پیشنهادهای ساختاریافته و دلایل ثبت‌شده در این قسمت نمایش داده می‌شوند.",
            ],
            [
              "Antipsychotic candidates",
              "داروهای ضدروان‌پریشی کاندید",
              "Ranked candidates and model shares appear here when available. Model share is not a probability of treatment success.",
              "کاندیدهای رتبه‌بندی‌شده و سهم مدل در صورت دسترسی نمایش داده می‌شوند. سهم مدل احتمال موفقیت درمان نیست.",
            ],
            [
              "Drug interactions",
              "تداخلات دارویی",
              "Keep risk rating, source evidence, and unavailable coverage visible.",
              "رتبه خطر، شواهد منبع و پوشش ناموجود به‌وضوح نمایش داده شوند.",
            ],
          ].map(([en, fa, desc, descFa]) => (
            <VStack key={en} gap={2}>
              <Heading level={3}>{t(en, fa)}</Heading>
              <Text type="supporting">{t(desc, descFa)}</Text>
            </VStack>
          ))}
          <details>
            <summary>{t("Advanced details", "جزئیات پیشرفته")}</summary>
            <Text type="supporting">
              {t(
                "Pinned model, content versions, proposal identity, and provenance remain inspectable.",
                "مدل ثابت، نسخه محتوا، شناسه پیشنهاد و منشأ داده‌ها قابل بررسی می‌مانند.",
              )}
            </Text>
          </details>
        </VStack>
        <VStack className="column" gap={5}>
          <HStack hAlign="between" gap={3} wrap="wrap">
            <Heading level={2}>
              {t("Physician final plan", "برنامه نهایی پزشک")}
            </Heading>
            <State tone="teal">{t("Editable", "قابل ویرایش")}</State>
          </HStack>
          <TextArea
            label={t("Final treatment plan", "برنامه نهایی درمان")}
            description={t(
              "Your plan is stored separately from the original proposal.",
              "برنامه شما جدا از پیشنهاد اصلی ذخیره می‌شود.",
            )}
            value={plan}
            onChange={setPlan}
            rows={10}
            placeholder={t(
              "Enter your reviewed plan…",
              "برنامه مرورشده خود را وارد کنید…",
            )}
          />
          <Button
            label={t(
              "Copy original into final plan · demo",
              "کپی پیشنهاد در برنامه نهایی · نمونه",
            )}
            onClick={() =>
              setPlan(
                t(
                  "Design preview only — no clinical proposal is available to copy.",
                  "فقط پیش‌نمایش طراحی — پیشنهاد بالینی برای کپی موجود نیست.",
                ),
              )
            }
          />
          <Divider />
          <CheckboxInput
            label={t(
              "I have reviewed the proposal and my final plan.",
              "پیشنهاد و برنامه نهایی خود را مرور کرده‌ام.",
            )}
            value={ack}
            onChange={setAck}
          />
          <Muted>
            {t(
              "Signing creates an immutable record. The existing confirmation and freshness checks remain required.",
              "امضا یک سابقه تغییرناپذیر ایجاد می‌کند. تأیید و بررسی به‌روز بودن همچنان الزامی است.",
            )}
          </Muted>
          <Button
            label={t("Preview signing confirmation", "پیش‌نمایش تأیید امضا")}
            variant="primary"
            isDisabled={!ack || !plan.trim()}
            onClick={() =>
              setMsg(
                t(
                  "Preview only: the real app requires its existing confirmation before signing. No record was signed.",
                  "فقط پیش‌نمایش: برنامه واقعی پیش از امضا تأیید فعلی را می‌خواهد. هیچ پرونده‌ای امضا نشد.",
                ),
              )
            }
          />
          {msg && <Text role="status">{msg}</Text>}
        </VStack>
      </HStack>
      <HStack className="action-bar" gap={3}>
        <Button
          label={t("Back to medications", "بازگشت به داروها")}
          onClick={() => go("medications")}
        />
      </HStack>
    </VStack>
  );
}
function Chart({ go }: { go: (v: View) => void }) {
  const t = useT();
  return (
    <VStack gap={6}>
      <Title
        title={t("Patient chart", "پرونده بیمار")}
        description={t(
          "Demo patient 02 · DEMO-002 · 36 years · Male",
          "بیمار نمونه ۰۲ · DEMO-002 · ۳۶ سال · مرد",
        )}
        action={
          <Button
            label={t("Start follow-up", "شروع پیگیری")}
            variant="primary"
            onClick={() => go("assessment")}
          />
        }
      />
      <HStack gap={3} wrap="wrap">
        <State>{t("Known case", "مورد شناخته‌شده")}</State>
        <State tone="teal">
          {t("Signed record · sample", "سابقه امضاشده · نمونه")}
        </State>
      </HStack>
      <HStack className="two-column" gap={6} vAlign="start">
        <VStack className="chart-history" gap={4}>
          <Heading level={2}>
            {t("Encounter history", "تاریخچه ویزیت‌ها")}
          </Heading>
          <VStack className="encounter-entry" gap={2} padding={4}>
            <Text weight="semibold">{t("Registration", "ثبت اولیه")}</Text>
            <Muted>
              <bdi dir="ltr">09 Nov 2026</bdi>
            </Muted>
            <State tone="teal">{t("Signed", "امضاشده")}</State>
          </VStack>
          <Muted>
            {t(
              "Illustrative encounter, not a real patient record.",
              "ویزیت ساختگی، بدون سابقه واقعی بیمار.",
            )}
          </Muted>
        </VStack>
        <VStack className="column" gap={5}>
          <HStack hAlign="between" gap={3}>
            <Heading level={2}>
              {t("Signed treatment plan", "برنامه درمانی امضاشده")}
            </Heading>
            <State>{t("Immutable", "تغییرناپذیر")}</State>
          </HStack>
          <Muted>
            {t(
              "Attribution and signing time remain attached to each record.",
              "نام امضاکننده و زمان امضا همراه هر سابقه حفظ می‌شود.",
            )}
          </Muted>
          <VStack className="signed-document" padding={6} gap={4}>
            <Heading level={3}>
              {t("Physician final plan", "برنامه نهایی پزشک")}
            </Heading>
            <Text>
              {t(
                "The signed plan text appears here, with its original line breaks and clinical content preserved.",
                "متن برنامه امضاشده با حفظ سطربندی و محتوای بالینی در اینجا نمایش داده می‌شود.",
              )}
            </Text>
            <Divider />
            <Muted>
              {t(
                "Signed by: Demo physician · Illustrative timestamp",
                "امضا: پزشک نمونه · زمان نمونه",
              )}
            </Muted>
          </VStack>
          <details>
            <summary>
              {t("Original system proposal", "پیشنهاد اصلی سیستم")}
            </summary>
            <Muted>
              {t(
                "The original proposal is retained separately and remains available for inspection.",
                "پیشنهاد اصلی به‌صورت جداگانه حفظ شده و برای بررسی در دسترس است.",
              )}
            </Muted>
          </details>
          <details>
            <summary>
              {t("Attributed addenda", "یادداشت‌های تکمیلی دارای نام نویسنده")}
            </summary>
            <Muted>
              {t(
                "Addenda are appended without changing the signed record.",
                "یادداشت‌ها بدون تغییر سابقه امضاشده افزوده می‌شوند.",
              )}
            </Muted>
          </details>
        </VStack>
      </HStack>
      <Notice>
        {t(
          "Research prototype. Signed records do not imply clinical validation.",
          "نمونه پژوهشی. امضای سوابق به معنای اعتبارسنجی بالینی نیست.",
        )}
      </Notice>
    </VStack>
  );
}
function Admin() {
  const t = useT(),
    [msg, setMsg] = useState("");
  return (
    <VStack gap={6}>
      <Title
        title={t("Provider settings", "تنظیمات ارائه‌دهنده")}
        description={t(
          "Administrator workspace · legacy reasoning provider configuration",
          "فضای مدیریت · پیکربندی ارائه‌دهنده مسیر استدلال قبلی",
        )}
      />
      <Notice>
        {t(
          "Provider settings apply to Known case and follow-up. New case uses the fixed model without a provider call.",
          "تنظیمات ارائه‌دهنده برای مورد شناخته‌شده و پیگیری است. مورد جدید از مدل ثابت بدون فراخوانی ارائه‌دهنده استفاده می‌کند.",
        )}
      </Notice>
      <VStack className="settings-form" gap={5}>
        <Heading level={2}>{t("Connection", "اتصال")}</Heading>
        <TextInput
          label={t("Provider URL", "نشانی ارائه‌دهنده")}
          value="https://provider.example/v1"
          onChange={() => {}}
        />
        <TextInput
          label={t("Model", "مدل")}
          value="Demo model"
          onChange={() => {}}
        />
        <TextInput
          label={t("API key", "کلید API")}
          type="password"
          value="sample-key-not-real"
          onChange={() => {}}
          description={t(
            "Stored keys remain masked. This preview contains no credentials.",
            "کلیدهای ذخیره‌شده پنهان می‌مانند. این نمونه شامل اطلاعات ورود نیست.",
          )}
        />
        <Divider />
        <Heading level={2}>{t("Capability check", "بررسی قابلیت‌ها")}</Heading>
        <Muted>
          {t(
            "Save a settings revision before testing the provider.",
            "پیش از آزمایش ارائه‌دهنده، نسخه تنظیمات را ذخیره کنید.",
          )}
        </Muted>
        <HStack gap={3} wrap="wrap">
          <Button
            label={t("Save settings · demo", "ذخیره تنظیمات · نمونه")}
            variant="primary"
            onClick={() =>
              setMsg(
                t(
                  "Preview only. No settings were saved and no provider was contacted.",
                  "فقط پیش‌نمایش. تنظیماتی ذخیره و درخواستی ارسال نشد.",
                ),
              )
            }
          />
          <Button label={t("Test provider", "آزمایش ارائه‌دهنده")} isDisabled />
        </HStack>
        {msg && <Text role="status">{msg}</Text>}
      </VStack>
    </VStack>
  );
}
function Login({ go }: { go: (v: View) => void }) {
  const t = useT(),
    [name, setName] = useState(""),
    [password, setPassword] = useState("");
  return (
    <HStack
      as="main"
      className="login-layout"
      gap={10}
      padding={6}
      vAlign="center"
    >
      <VStack className="login-intro" gap={6}>
        <Text className="brand-large" weight="bold">
          X-INSIGHT
        </Text>
        <Heading level={1}>
          {t(
            "A workspace for thoughtful clinical review.",
            "فضایی برای مرور سنجیده بالینی.",
          )}
        </Heading>
        <Text>
          {t(
            "Structured assessment, traceable proposals, and physician-led treatment planning.",
            "ارزیابی ساختاریافته، پیشنهادهای قابل پیگیری و برنامه‌ریزی درمان به هدایت پزشک.",
          )}
        </Text>
        <Divider />
        <Muted>
          {t(
            "A research prototype for exploring schizophrenia treatment options. Not clinically validated.",
            "نمونه پژوهشی برای بررسی گزینه‌های درمان اسکیزوفرنی. از نظر بالینی اعتبارسنجی نشده است.",
          )}
        </Muted>
      </VStack>
      <VStack className="login-form" gap={5} padding={6}>
        <Heading level={2}>{t("Sign in", "ورود")}</Heading>
        <Muted>
          {t(
            "Use your assigned workspace account.",
            "از حساب اختصاص‌یافته خود استفاده کنید.",
          )}
        </Muted>
        <TextInput
          label={t("Username", "نام کاربری")}
          value={name}
          onChange={setName}
        />
        <TextInput
          label={t("Password", "گذرواژه")}
          type="password"
          value={password}
          onChange={setPassword}
        />
        <Button
          label={t("Enter design preview", "ورود به پیش‌نمایش طراحی")}
          variant="primary"
          onClick={() => go("patients")}
        />
        <ReviewLabel />
      </VStack>
    </HStack>
  );
}
function App() {
  const [fa, setFa] = useState(false),
    [dark, setDark] = useState(false),
    [view, setView] = useState<View>("patients");
  const t = (en: string, persian: string) => (fa ? persian : en);
  useEffect(() => {
    document.documentElement.lang = fa ? "fa" : "en";
  }, [fa]);
  const admin = view === "admin";
  const encounter = ["assessment", "medications", "proposal"].includes(view);
  const go = (v: View) => setView(v);
  return (
    <Language.Provider value={fa}>
      <Theme theme={academic} mode="light">
        <VStack className="review-root" gap={3}>
          <HStack
            as="header"
            className="review-toolbar"
            gap={3}
            hAlign="between"
            vAlign="center"
            wrap="wrap"
          >
            <VStack gap={1}>
              <Text weight="semibold">
                {t("X-INSIGHT · Design review", "X-INSIGHT · مرور طراحی")}
              </Text>
              <Muted>
                {t(
                  "Direction 01 · Academic clinical workspace",
                  "طرح ۰۱ · فضای پژوهش بالینی",
                )}
              </Muted>
            </VStack>
            <HStack gap={2} wrap="wrap" vAlign="center">
              <select
                aria-label="Preview screen"
                value={view}
                onChange={(e) => setView(e.target.value as View)}
              >
                {views.map(([id, en, persian]) => (
                  <option key={id} value={id}>
                    {t(en, persian)}
                  </option>
                ))}
              </select>
              <Button
                label={fa ? "English" : "فارسی"}
                size="sm"
                onClick={() => setFa(!fa)}
              />
              <Button
                label={dark ? t("Light", "روشن") : t("Dark", "تیره")}
                size="sm"
                onClick={() => setDark(!dark)}
              />
            </HStack>
          </HStack>
          <Theme theme={academic} mode={dark ? "dark" : "light"}>
            <VStack
              className="product-frame"
              gap={0}

              dir={fa ? "rtl" : "ltr"}
            >
              {view === "login" ? (
                <Login go={go} />
              ) : (
                <AppShell
                  height="auto"
                  variant="section"
                  contentPadding={0}
                  mobileNav={false}
                  sideNav={
                    <SideNav
                      header={
                        <VStack gap={2} padding={4}>
                          <SideNavHeading
                            heading="X-INSIGHT"
                            subheading={t("Research workspace", "فضای پژوهشی")}
                          />
                        </VStack>
                      }
                      footer={
                        <VStack padding={4} gap={3}>
                          <Divider />
                          <Text weight="semibold">
                            {t(
                              admin ? "Demo administrator" : "Demo physician",
                              admin ? "مدیر نمونه" : "پزشک نمونه",
                            )}
                          </Text>
                          <Muted>
                            {t(
                              admin ? "Administrator" : "Physician",
                              admin ? "مدیر" : "پزشک",
                            )}
                          </Muted>
                          <Button
                            label={t("Sign out", "خروج")}
                            variant="ghost"
                            size="sm"
                            onClick={() => go("login")}
                          />
                        </VStack>
                      }
                    >
                      <SideNavSection
                        title={t(
                          admin ? "Administration" : "Clinical workspace",
                          admin ? "مدیریت" : "فضای بالینی",
                        )}
                      >
                        <SideNavItem
                          label={t("Dashboard", "داشبورد")}
                          onClick={() => go("patients")}
                        />
                        <SideNavItem
                          label={t("Patients", "بیماران")}
                          isSelected={!admin}
                          onClick={() => go("patients")}
                        />
                        {!admin && (
                          <>
                            <SideNavItem
                              label={t("Unavailable drugs", "داروهای ناموجود")}
                              isDisabled
                            />
                            <SideNavItem
                              label={t("Account", "حساب کاربری")}
                              isDisabled
                            />
                          </>
                        )}
                        {admin &&
                          [
                            "Physicians",
                            "Networks",
                            "Audit",
                            "Exports",
                            "Backups",
                            "Restores",
                            "Provider",
                            "Status",
                            "Account",
                          ].map((n, i) => (
                            <SideNavItem
                              key={n}
                              label={t(
                                n,
                                [
                                  "پزشکان",
                                  "شبکه‌ها",
                                  "ممیزی",
                                  "خروجی‌ها",
                                  "پشتیبان‌ها",
                                  "بازیابی",
                                  "ارائه‌دهنده",
                                  "وضعیت",
                                  "حساب کاربری",
                                ][i],
                              )}
                              isSelected={n === "Provider"}
                              isDisabled={n !== "Provider"}
                              onClick={() => go("admin")}
                            />
                          ))}
                      </SideNavSection>
                    </SideNav>
                  }
                >
                  <Layout
                    height="auto"
                    contentWidth="var(--xi-content-width)"
                    content={
                      <LayoutContent padding={6}>
                        <VStack gap={6}>
                          <HStack
                            hAlign="between"
                            vAlign="center"
                            gap={3}
                            wrap="wrap"
                            className="context-bar"
                          >
                            <Text type="supporting" color="secondary">
                              {t(
                                admin
                                  ? "Administration / Provider"
                                  : encounter
                                    ? "Patients / New case"
                                    : view === "chart"
                                      ? "Patients / Chart"
                                      : "Clinical workspace / Patients",
                                admin
                                  ? "مدیریت / ارائه‌دهنده"
                                  : encounter
                                    ? "بیماران / مورد جدید"
                                    : view === "chart"
                                      ? "بیماران / پرونده"
                                      : "فضای بالینی / بیماران",
                              )}
                            </Text>
                            <Text type="supporting" className="research-label">
                              {t("Research prototype", "نمونه پژوهشی")}
                            </Text>
                          </HStack>
                          {view === "patients" ? (
                            <Patients go={go} />
                          ) : view === "assessment" ? (
                            <Assessment go={go} />
                          ) : view === "medications" ? (
                            <Medications go={go} />
                          ) : view === "proposal" ? (
                            <Proposal go={go} />
                          ) : view === "chart" ? (
                            <Chart go={go} />
                          ) : (
                            <Admin />
                          )}
                        </VStack>
                      </LayoutContent>
                    }
                  />
                </AppShell>
              )}
            </VStack>
          </Theme>
          <HStack
            as="footer"
            className="review-footer"
            hAlign="between"
            gap={3}
            wrap="wrap"
          >
            <ReviewLabel />
            <Muted>
              {t(
                "UI proposal · awaiting approval",
                "پیشنهاد رابط کاربری · در انتظار تأیید",
              )}
            </Muted>
          </HStack>
        </VStack>
      </Theme>
    </Language.Provider>
  );
}
const css = `
html,body,#root{display:block;margin:0;background:transparent;min-width:0}body{font-family:var(--font-sans,Arial,sans-serif)}*{box-sizing:border-box}
.review-root{color:var(--color-text-primary);font-family:var(--font-family-body)}
.review-toolbar,.review-footer{padding-block:var(--spacing-3);color:var(--foreground,var(--color-text-primary))}.review-toolbar .astryx-text,.review-footer .astryx-text{color:inherit}
.review-toolbar select{max-width:100%}.review-footer{padding-bottom:var(--spacing-4)}
.product-frame{border:var(--xi-line) solid var(--color-border);border-radius:var(--radius-container);overflow:hidden;background:var(--color-background-surface);min-height:var(--xi-page-min-height)}
.product-frame .astryx-app-shell{min-height:var(--xi-page-min-height);background:var(--color-background-surface)}
.product-frame .astryx-side-nav{width:var(--xi-rail-width);min-width:var(--xi-rail-width);background:var(--color-background-page);border-inline-end:var(--xi-line) solid var(--color-border)}
.product-frame .astryx-side-nav-heading{padding:0}.product-frame .astryx-side-nav-heading *{font-family:var(--font-family-body)}
.context-bar{padding-block-end:var(--spacing-4);border-bottom:var(--xi-line) solid var(--color-border)}
h1{font-size:var(--xi-type-title)!important;line-height:1.25!important;font-weight:500!important}h2{font-size:var(--xi-type-section)!important;line-height:1.4!important;font-weight:500!important}h3{font-size:var(--xi-type-base)!important;font-family:var(--font-family-body)!important;font-weight:600!important}
[dir=rtl] h1,[dir=rtl] h2{font-family:Tahoma,Arial,sans-serif!important} .title-row{align-items:center}.title-row> :first-child{flex:1;min-width:0}
select{background:var(--color-background-surface);color:var(--color-text-primary);border:var(--xi-line) solid var(--color-border);border-radius:var(--radius-element);padding:var(--spacing-2) var(--spacing-3);font:inherit;min-height:var(--spacing-10)}
button:focus-visible,select:focus-visible,summary:focus-visible,input:focus-visible{outline:calc(var(--xi-line)*2) solid var(--color-accent);outline-offset:var(--spacing-1)}
.filters{padding-block-end:var(--spacing-2)}.table-frame{margin:0!important;width:100%!important;border:var(--xi-line) solid var(--color-border);border-radius:var(--radius-element);overflow:hidden}.table-frame .astryx-table{width:100%}.table-frame th{background:var(--color-background-page);font-weight:600}.table-frame td{height:var(--xi-row-height);vertical-align:middle}.table-footer{border-top:var(--xi-line) solid var(--color-border);background:var(--color-background-surface)}
.state{display:inline-flex;width:fit-content;align-items:center;border:var(--xi-line) solid var(--color-border);border-radius:var(--radius-inner);padding:var(--spacing-1) var(--spacing-2);white-space:nowrap;background:var(--color-background-page);color:var(--color-text-secondary);font-size:var(--xi-type-small)}.state.teal{color:var(--xi-teal-ink);background:var(--xi-teal-surface);border-color:transparent}.state.warning{color:var(--xi-warning-ink);background:var(--xi-warning-surface);border-color:transparent}
.notice{background:var(--color-background-page);border-inline-start:calc(var(--xi-line)*2) solid var(--xi-slate);padding:var(--spacing-4);border-radius:var(--radius-inner);line-height:1.6}.notice.warning{background:var(--xi-warning-surface);color:var(--xi-warning-ink);border-color:var(--xi-warning-ink)}.research-label{color:var(--color-text-secondary);border:var(--xi-line) solid var(--color-border);padding:var(--spacing-1) var(--spacing-2);border-radius:var(--radius-inner);font-size:var(--xi-type-small)}
.design-details{font-size:var(--xi-type-small);color:var(--color-text-secondary)}summary{cursor:pointer;line-height:1.7;font-size:var(--xi-type-base)}details> :not(summary){margin-block-start:var(--spacing-3)}.empty{padding:var(--spacing-10);text-align:center;align-items:center;border:var(--xi-line) solid var(--color-border)}
.step-track{display:grid!important;grid-template-columns:repeat(7,minmax(0,1fr));border-bottom:var(--xi-line) solid var(--color-border)}.step{background:transparent;border:0;border-bottom:calc(var(--xi-line)*2) solid transparent;color:var(--color-text-secondary);padding:var(--spacing-3) var(--spacing-1);display:flex;flex-direction:column;align-items:center;gap:var(--spacing-2);font:inherit;cursor:pointer;min-width:0}.step.active{border-color:var(--color-accent);color:var(--xi-teal-ink);background:var(--xi-teal-surface)}.step-number{display:grid;place-items:center;border:var(--xi-line) solid var(--color-border);border-radius:50%;width:var(--xi-icon-size);height:var(--xi-icon-size)}.step.active .step-number{background:var(--color-accent);color:var(--color-on-accent);border-color:transparent}
.assessment-main{flex:1;min-width:0}.results-rail{width:var(--xi-review-width);flex-shrink:0;border:var(--xi-line) solid var(--color-border);border-radius:var(--radius-element);background:var(--color-background-page)}.results-rail h2{font-family:var(--font-family-body)!important;font-size:var(--xi-type-base)!important;font-weight:600!important}.subscale-tabs{border-bottom:var(--xi-line) solid var(--color-border);padding-bottom:var(--spacing-3)}.worksheet{border-top:var(--xi-line) solid var(--color-border)}.rating-row{border:0;border-bottom:var(--xi-line) solid var(--color-border);margin:0;padding:var(--spacing-3) 0;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:var(--spacing-2)}.rating-row legend{float:inline-start;grid-column:1 / -1;line-height:1.7}.rating-row legend>*:first-child{margin-inline-end:var(--spacing-2)}.rating-row> :last-child{align-self:center}.rating-options{flex-wrap:wrap}.rating-options label{position:relative;cursor:pointer;min-width:var(--spacing-8);min-height:var(--spacing-8);display:grid;place-items:center;border:var(--xi-line) solid var(--color-border);border-radius:var(--radius-inner)}.rating-options label:has(input:checked){background:var(--color-accent);color:var(--color-on-accent);border-color:var(--color-accent)}.rating-options label:has(input:checked) .astryx-text{color:var(--color-on-accent)}.rating-options input{appearance:none;position:absolute;inset:0;margin:0;cursor:pointer}.rating-options label:has(input:focus-visible){outline:var(--xi-line) solid var(--color-accent);outline-offset:var(--spacing-1)}
.action-bar{border-top:var(--xi-line) solid var(--color-border);padding-top:var(--spacing-5);align-items:center}.two-column>.column{flex:1;min-width:0}.evidence,.proposal-original{border:var(--xi-line) solid var(--color-border);border-radius:var(--radius-element);background:var(--color-background-page)}.medication-row{border-bottom:var(--xi-line) solid var(--color-border)}.search-results{border:var(--xi-line) solid var(--color-border)}.chart-history{width:var(--xi-field-width);flex-shrink:0}.encounter-entry{border-inline-start:var(--spacing-1) solid var(--color-accent);background:var(--xi-teal-surface)}.signed-document{border:var(--xi-line) solid var(--color-border);line-height:1.8}.settings-form{max-width:var(--xi-reading-width)}
.login-layout{min-height:var(--xi-page-min-height)}.login-intro{flex:1;min-width:0;max-width:var(--xi-auth-width);margin-inline:auto}.login-intro h1{font-size:calc(var(--xi-type-title)*1.35)!important}.brand-large{letter-spacing:.06em;color:var(--xi-slate)}.login-form{flex:1;max-width:var(--xi-auth-width);border:var(--xi-line) solid var(--color-border);border-radius:var(--radius-container);background:var(--color-background-page);margin-inline:auto}
@media(max-width:78.57em){.assessment-layout{flex-direction:column!important}.results-rail{width:100%}.assessment-main{width:100%}.two-column{flex-direction:column!important}.two-column>.column{width:100%}.chart-history{width:100%}}
@media(max-width:54.28em){.product-frame .astryx-side-nav{width:calc(var(--xi-rail-width)*.82);min-width:calc(var(--xi-rail-width)*.82)}.step-track{grid-template-columns:repeat(4,minmax(0,1fr))}.login-layout{flex-direction:column!important;align-items:stretch!important}.login-intro,.login-form{width:100%;max-width:none}.rating-row{grid-template-columns:1fr}.rating-row> :last-child{grid-column:1}.title-row{align-items:start!important}.table-frame{overflow-x:auto}.review-toolbar{align-items:start!important}}
@media(max-width:37.14em){.product-frame .astryx-side-nav{display:none}.product-frame .astryx-app-shell{display:block!important}.step-track{grid-template-columns:repeat(3,minmax(0,1fr))}.review-toolbar{flex-direction:column}.review-footer{flex-direction:column}.rating-options{gap:var(--spacing-1)!important}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;scroll-behavior:auto!important}}
`;
const style = document.createElement("style");
style.textContent = css;
document.head.append(style);
createRoot(document.getElementById("root")!).render(<App />);
