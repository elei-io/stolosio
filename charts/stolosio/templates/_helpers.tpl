{{- define "stolosio.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "stolosio.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else if contains (include "stolosio.name" .) .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "stolosio.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "stolosio.labels" -}}
app.kubernetes.io/name: {{ include "stolosio.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" | quote }}
{{- end }}

{{- define "stolosio.selectorLabels" -}}
app.kubernetes.io/name: {{ include "stolosio.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "stolosio.extraPodLabels" -}}
{{- $labels := omit .Values.podLabels
  "app.kubernetes.io/name"
  "app.kubernetes.io/instance"
  "app.kubernetes.io/component" }}
{{- with $labels }}
{{- toYaml . }}
{{- end }}
{{- end }}

{{- define "stolosio.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (printf "%s-fleet-controller" (include "stolosio.fullname" .)) .Values.serviceAccount.name }}
{{- else }}
{{- required "serviceAccount.name is required when serviceAccount.create=false" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "stolosio.connectionEnv" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ required "database.existingSecret is required" .Values.database.existingSecret }}
      key: {{ .Values.database.urlSecretKey }}
- name: NATS_URL
  valueFrom:
    secretKeyRef:
      name: {{ required "nats.existingSecret is required" .Values.nats.existingSecret }}
      key: {{ .Values.nats.urlSecretKey }}
{{- with .Values.nats.seedSecretKey }}
- name: NATS_SEED
  valueFrom:
    secretKeyRef:
      name: {{ $.Values.nats.existingSecret }}
      key: {{ . }}
{{- end }}
- name: JETSTREAM_EVENT_REPLICAS
  value: {{ .Values.nats.jetstreamReplicas | quote }}
{{- end }}

{{- define "stolosio.browserbaseEnv" -}}
- name: BROWSERBASE_NETWORK_ISOLATION_VERIFIED
  value: {{ .Values.browserbase.networkIsolationVerified | quote }}
- name: BROWSERBASE_API_URL
  value: {{ .Values.browserbase.apiUrl | quote }}
{{- with .Values.browserbase.existingSecret }}
- name: BROWSERBASE_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $.Values.browserbase.apiKeySecretKey }}
- name: BROWSERBASE_PROJECT_ID
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $.Values.browserbase.projectIdSecretKey }}
{{- end }}
{{- end }}

{{- define "stolosio.commonPodSpec" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
