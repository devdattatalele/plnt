{{/*
  Common labels + name helpers for vllm-runtime.
*/}}

{{- define "vllm-runtime.name" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "vllm-runtime.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "vllm-runtime.labels" -}}
app.kubernetes.io/name: {{ include "vllm-runtime.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: inference-runtime
app.kubernetes.io/part-of: plnt
app.kubernetes.io/managed-by: {{ .Release.Service }}
plnt.work/runtime: vllm
plnt.work/model: {{ .Values.model.name | quote }}
{{- end -}}

{{- define "vllm-runtime.selectorLabels" -}}
app.kubernetes.io/name: {{ include "vllm-runtime.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
