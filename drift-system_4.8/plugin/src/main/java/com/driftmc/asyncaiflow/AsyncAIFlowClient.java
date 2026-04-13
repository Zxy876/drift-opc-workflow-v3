package com.driftmc.asyncaiflow;

import java.io.IOException;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

/**
 * AsyncAIFlowClient
 *
 * MC Plugin 侧向 AsyncAIFlow Runtime (:8080) 提交 Drift 专案 issue 的 HTTP 客户端。
 * 只做一件事：POST /planner/execute，异步回调 workflowId 或错误信息。
 */
public class AsyncAIFlowClient {

    private static final Gson GSON = new Gson();
    private static final MediaType JSON_MEDIA = MediaType.parse("application/json; charset=utf-8");

    private final OkHttpClient http;
    private final String baseUrl;

    public AsyncAIFlowClient(String baseUrl) {
        this.baseUrl = baseUrl.replaceAll("/$", "");
        this.http = new OkHttpClient.Builder()
                .connectTimeout(5, TimeUnit.SECONDS)
                .readTimeout(30, TimeUnit.SECONDS)
                .writeTimeout(15, TimeUnit.SECONDS)
                .build();
    }

    /**
     * 异步提交 issue 到 AsyncAIFlow /planner/execute。
     *
     * @param issue       玩家输入的原始文本
     * @param repoContext 仓库上下文（例如 "drift-system"）
     * @param difficulty  难度评分 1-5（透传给 planner 路由）
     * @param playerId    玩家名（用于 drift_refresh 回写）
     * @param onSuccess   成功回调，接收 workflowId
     * @param onError     失败回调，接收错误描述字符串
     */
    public void submitIssue(String issue, String repoContext, int difficulty, String playerId,
                            Consumer<Long> onSuccess, Consumer<String> onError) {
        JsonObject body = new JsonObject();
        body.addProperty("issue", issue);
        body.addProperty("repo_context", repoContext != null ? repoContext : "drift-system");
        body.addProperty("difficulty", difficulty);
        body.addProperty("player_id", playerId != null ? playerId : "unknown");

        Request request = new Request.Builder()
                .url(baseUrl + "/planner/execute")
            .post(RequestBody.create(JSON_MEDIA, GSON.toJson(body)))
                .build();

        http.newCall(request).enqueue(new Callback() {
            @Override
            public void onFailure(Call call, IOException e) {
                onError.accept("AsyncAIFlow unreachable: " + e.getMessage());
            }

            @Override
            public void onResponse(Call call, Response response) throws IOException {
                try (response) {
                    String raw = response.body() != null ? response.body().string() : "{}";
                    JsonObject root;
                    try {
                        root = JsonParser.parseString(raw).getAsJsonObject();
                    } catch (Exception ex) {
                        onError.accept("AsyncAIFlow response parse error: " + ex.getMessage());
                        return;
                    }

                    boolean success = root.has("success") && root.get("success").getAsBoolean();
                    if (!success) {
                        String msg = root.has("message") ? root.get("message").getAsString() : raw;
                        onError.accept("AsyncAIFlow rejected: " + msg);
                        return;
                    }

                    if (!root.has("data") || !root.get("data").isJsonObject()) {
                        onError.accept("AsyncAIFlow response missing 'data' field");
                        return;
                    }

                    JsonObject data = root.getAsJsonObject("data");
                    if (!data.has("workflowId")) {
                        onError.accept("AsyncAIFlow response missing 'workflowId'");
                        return;
                    }

                    long workflowId = data.get("workflowId").getAsLong();
                    onSuccess.accept(workflowId);
                } catch (Exception ex) {
                    onError.accept("AsyncAIFlow client error: " + ex.getMessage());
                }
            }
        });
    }

    /**
     * 异步提交 drift_experience 工作流到 AsyncAIFlow /planner/execute。
     * 使用 repo_context="drift_experience" 触发 Beam Search + 叙事弧生成。
     *
     * @param premise     玩家输入的关卡描述文本
     * @param difficulty  难度评分 3-5
     * @param playerId    玩家名（用于 drift_refresh 回写）
     * @param onSuccess   成功回调，接收 workflowId
     * @param onError     失败回调，接收错误描述字符串
     */
    public void submitDriftExperience(String premise, int difficulty, String playerId,
                                       Consumer<Long> onSuccess, Consumer<String> onError) {
        JsonObject body = new JsonObject();
        body.addProperty("issue", premise);
        body.addProperty("repo_context", "drift_experience");
        body.addProperty("difficulty", Math.max(3, difficulty));
        body.addProperty("player_id", playerId != null ? playerId : "unknown");

        Request request = new Request.Builder()
                .url(baseUrl + "/planner/execute")
                .post(RequestBody.create(JSON_MEDIA, GSON.toJson(body)))
                .build();

        http.newCall(request).enqueue(new Callback() {
            @Override
            public void onFailure(Call call, IOException e) {
                onError.accept("AsyncAIFlow drift_experience unreachable: " + e.getMessage());
            }

            @Override
            public void onResponse(Call call, Response response) throws IOException {
                try (response) {
                    String raw = response.body() != null ? response.body().string() : "{}";
                    JsonObject root;
                    try {
                        root = JsonParser.parseString(raw).getAsJsonObject();
                    } catch (Exception ex) {
                        onError.accept("drift_experience response parse error: " + ex.getMessage());
                        return;
                    }

                    boolean success = root.has("success") && root.get("success").getAsBoolean();
                    if (!success) {
                        String msg = root.has("message") ? root.get("message").getAsString() : raw;
                        onError.accept("drift_experience rejected: " + msg);
                        return;
                    }

                    if (!root.has("data") || !root.get("data").isJsonObject()) {
                        onError.accept("drift_experience response missing 'data' field");
                        return;
                    }

                    JsonObject data = root.getAsJsonObject("data");
                    if (!data.has("workflowId")) {
                        onError.accept("drift_experience response missing 'workflowId'");
                        return;
                    }

                    long workflowId = data.get("workflowId").getAsLong();
                    onSuccess.accept(workflowId);
                } catch (Exception ex) {
                    onError.accept("drift_experience client error: " + ex.getMessage());
                }
            }
        });
    }

    /**
     * 同步查询 workflow 当前状态（用于 MC 内进度轮询）。
     * 返回 status 字符串（RUNNING / SUCCEEDED / FAILED / ...），失败返回 null。
     */
    public String getWorkflowStatus(long workflowId) {
        Request request = new Request.Builder()
                .url(baseUrl + "/workflows/" + workflowId)
                .get()
                .build();

        try (Response response = http.newCall(request).execute()) {
            if (!response.isSuccessful() || response.body() == null) {
                return null;
            }
            String raw = response.body().string();
            JsonObject root = JsonParser.parseString(raw).getAsJsonObject();
            if (!root.has("data")) return null;
            JsonObject data = root.getAsJsonObject("data");
            return data.has("status") ? data.get("status").getAsString() : null;
        } catch (Exception e) {
            return null;
        }
    }
}
