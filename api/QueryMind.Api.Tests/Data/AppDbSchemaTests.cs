using Microsoft.EntityFrameworkCore;
using Npgsql;
using QueryMind.Api.Data;
using Testcontainers.PostgreSql;

namespace QueryMind.Api.Tests.Data;

/// <summary>A throwaway PostgreSQL 16 with the app DB migrations applied (design §5.1).</summary>
public sealed class MigratedDatabase : IAsyncLifetime
{
    private readonly PostgreSqlContainer _container = new PostgreSqlBuilder("postgres:16").Build();

    public string ConnectionString => _container.GetConnectionString();

    public async Task InitializeAsync()
    {
        await _container.StartAsync();
        await using var db = CreateContext();
        await db.Database.MigrateAsync();
    }

    public Task DisposeAsync() => _container.DisposeAsync().AsTask();

    public AppDbContext CreateContext() =>
        new(AppDbContext.Configure(new DbContextOptionsBuilder<AppDbContext>(), ConnectionString).Options);
}

public sealed class AppDbSchemaTests(MigratedDatabase database) : IClassFixture<MigratedDatabase>
{
    // --- tables and columns ---

    [Fact]
    public async Task Migrations_CreateTheThreeTables()
    {
        var tables = await Strings(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' " +
            "AND table_name <> '__EFMigrationsHistory' ORDER BY 1");

        Assert.Equal(["query_attempts", "query_history", "users"], tables);
    }

    [Theory]
    [InlineData("users", "id:uuid:NO,email:citext:NO,password_hash:text:NO,role:text:NO,created_at:timestamp with time zone:NO")]
    [InlineData("query_attempts", "id:uuid:NO,history_id:uuid:NO,attempt_no:smallint:NO,sql:text:YES,stage:text:NO,error_code:text:YES,error:text:YES,latency_ms:integer:NO,prompt_tokens:integer:YES,completion_tokens:integer:YES")]
    [InlineData("query_history", "id:uuid:NO,user_id:uuid:NO,question:text:NO,status:text:NO,final_sql:text:YES,message:text:YES,row_count:integer:YES,truncated:boolean:YES,chart_type:text:YES,linking_ms:integer:YES,generation_ms:integer:YES,validation_ms:integer:YES,execution_ms:integer:YES,summary_ms:integer:YES,engine_total_ms:integer:YES,total_ms:integer:YES,prompt_tokens:integer:YES,completion_tokens:integer:YES,model:text:YES,correlation_id:text:NO,rerun_of_id:uuid:YES,created_at:timestamp with time zone:NO")]
    public async Task Columns_MatchDesign(string table, string expected)
    {
        var columns = await Strings(
            "SELECT column_name || ':' || CASE WHEN data_type = 'USER-DEFINED' THEN udt_name ELSE data_type END " +
            "|| ':' || is_nullable FROM information_schema.columns WHERE table_name = @t ORDER BY ordinal_position",
            ("t", table));

        Assert.Equal(expected.Split(','), columns);
    }

    [Fact]
    public async Task Defaults_RoleIsUserAndTimestampsAreSet()
    {
        await using var conn = await Open();
        var userId = await InsertUser(conn, "defaults@example.com", role: null);
        await using var cmd = new NpgsqlCommand("SELECT role, created_at > now() - interval '1 minute' FROM users WHERE id = @id", conn);
        cmd.Parameters.AddWithValue("id", userId);
        await using var reader = await cmd.ExecuteReaderAsync();
        Assert.True(await reader.ReadAsync());
        Assert.Equal("user", reader.GetString(0));
        Assert.True(reader.GetBoolean(1));
    }

    // --- CHECK constraints ---

    [Theory]
    [InlineData("superuser")]
    [InlineData("Admin")]
    public async Task Check_RejectsUnknownRole(string role)
    {
        await using var conn = await Open();
        var error = await Assert.ThrowsAsync<PostgresException>(() => InsertUser(conn, $"{Guid.NewGuid()}@example.com", role));
        Assert.Equal(PostgresErrorCodes.CheckViolation, error.SqlState);
        Assert.Equal("ck_users_role", error.ConstraintName);
    }

    [Fact]
    public async Task Check_AcceptsEveryDesignedStatusAndRejectsOthers()
    {
        await using var conn = await Open();
        var userId = await InsertUser(conn, $"{Guid.NewGuid()}@example.com");
        foreach (var status in HistoryStatus.All)
        {
            await InsertHistory(conn, userId, status);
        }

        var error = await Assert.ThrowsAsync<PostgresException>(() => InsertHistory(conn, userId, "pending"));
        Assert.Equal(PostgresErrorCodes.CheckViolation, error.SqlState);
        Assert.Equal("ck_query_history_status", error.ConstraintName);
    }

    [Theory]
    [InlineData("generate", 1, "ck_query_attempts_stage")]
    [InlineData("execute", 0, "ck_query_attempts_attempt_no")]
    public async Task Check_RejectsBadAttemptStageOrNumber(string stage, short attemptNo, string constraint)
    {
        await using var conn = await Open();
        var historyId = await InsertHistory(conn, await InsertUser(conn, $"{Guid.NewGuid()}@example.com"));
        var error = await Assert.ThrowsAsync<PostgresException>(() => InsertAttempt(conn, historyId, attemptNo, stage));
        Assert.Equal(PostgresErrorCodes.CheckViolation, error.SqlState);
        Assert.Equal(constraint, error.ConstraintName);
    }

    // --- uniqueness ---

    [Fact]
    public async Task Email_IsUniqueIgnoringCase()
    {
        await using var conn = await Open();
        var local = Guid.NewGuid().ToString("N");
        await InsertUser(conn, $"{local}@example.com");
        var error = await Assert.ThrowsAsync<PostgresException>(() => InsertUser(conn, $"{local.ToUpperInvariant()}@EXAMPLE.com"));
        Assert.Equal(PostgresErrorCodes.UniqueViolation, error.SqlState);
    }

    [Fact]
    public async Task AttemptNo_IsUniquePerHistory()
    {
        await using var conn = await Open();
        var userId = await InsertUser(conn, $"{Guid.NewGuid()}@example.com");
        var first = await InsertHistory(conn, userId);
        var second = await InsertHistory(conn, userId);
        await InsertAttempt(conn, first, 1);
        await InsertAttempt(conn, second, 1); // same number, different history: fine

        var error = await Assert.ThrowsAsync<PostgresException>(() => InsertAttempt(conn, first, 1));
        Assert.Equal(PostgresErrorCodes.UniqueViolation, error.SqlState);
        Assert.Equal("ix_query_attempts_history_id_attempt_no", error.ConstraintName);
    }

    // --- foreign keys ---

    [Fact]
    public async Task ForeignKeys_HaveTheDesignedDeleteRules()
    {
        var rules = await Strings(
            "SELECT tc.table_name || '.' || kcu.column_name || '->' || ccu.table_name || '.' || ccu.column_name || ':' || rc.delete_rule " +
            "FROM information_schema.referential_constraints rc " +
            "JOIN information_schema.table_constraints tc ON tc.constraint_name = rc.constraint_name " +
            "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = rc.constraint_name " +
            "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = rc.unique_constraint_name " +
            "ORDER BY 1");

        Assert.Equal(
        [
            "query_attempts.history_id->query_history.id:CASCADE",
            "query_history.rerun_of_id->query_history.id:SET NULL",
            "query_history.user_id->users.id:RESTRICT",
        ], rules);
    }

    [Fact]
    public async Task ForeignKey_RejectsHistoryForUnknownUser()
    {
        await using var conn = await Open();
        var error = await Assert.ThrowsAsync<PostgresException>(() => InsertHistory(conn, Guid.NewGuid()));
        Assert.Equal(PostgresErrorCodes.ForeignKeyViolation, error.SqlState);
    }

    [Fact]
    public async Task DeletingHistory_CascadesToAttempts()
    {
        await using var conn = await Open();
        var historyId = await InsertHistory(conn, await InsertUser(conn, $"{Guid.NewGuid()}@example.com"));
        await InsertAttempt(conn, historyId, 1);
        await InsertAttempt(conn, historyId, 2);

        await Execute(conn, "DELETE FROM query_history WHERE id = @id", ("id", historyId));

        Assert.Equal(0L, await Scalar<long>(conn, "SELECT count(*) FROM query_attempts WHERE history_id = @id", ("id", historyId)));
    }

    [Fact]
    public async Task DeletingAUserWithHistory_IsRefused()
    {
        await using var conn = await Open();
        var userId = await InsertUser(conn, $"{Guid.NewGuid()}@example.com");
        await InsertHistory(conn, userId);

        var error = await Assert.ThrowsAsync<PostgresException>(() =>
            Execute(conn, "DELETE FROM users WHERE id = @id", ("id", userId)));
        Assert.Equal(PostgresErrorCodes.ForeignKeyViolation, error.SqlState);
    }

    [Fact]
    public async Task DeletingAnOriginal_KeepsItsRerunAndClearsTheLink()
    {
        await using var conn = await Open();
        var userId = await InsertUser(conn, $"{Guid.NewGuid()}@example.com");
        var original = await InsertHistory(conn, userId);
        var rerun = await InsertHistory(conn, userId, rerunOf: original);

        await Execute(conn, "DELETE FROM query_history WHERE id = @id", ("id", original));

        Assert.True(await Scalar<bool>(conn, "SELECT rerun_of_id IS NULL FROM query_history WHERE id = @id", ("id", rerun)));
    }

    // --- indexes ---

    [Fact]
    public async Task Indexes_SupportHistoryListsAndReports()
    {
        var indexes = await Strings(
            "SELECT indexname || ' ' || substring(indexdef from '\\((.*)\\)') FROM pg_indexes " +
            "WHERE tablename = 'query_history' AND indexname LIKE 'ix_%' ORDER BY 1");

        Assert.Contains("ix_query_history_user_id_created_at user_id, created_at DESC", indexes);
        Assert.Contains("ix_query_history_created_at created_at", indexes);
    }

    // --- EF round trip (the model and the migration agree) ---

    [Fact]
    public async Task EfCore_RoundTripsAHistoryWithAttempts()
    {
        var user = new User { Email = $"{Guid.NewGuid()}@example.com", PasswordHash = "hash" };
        var history = new QueryHistory
        {
            User = user,
            Question = "How many customers are there?",
            Status = HistoryStatus.Success,
            FinalSql = "SELECT count(*) FROM customers",
            RowCount = 1,
            TotalMs = 16097,
            PromptTokens = 3027,
            CompletionTokens = 77,
            CorrelationId = Guid.NewGuid().ToString(),
        };
        history.Attempts.Add(new QueryAttempt { AttemptNo = 1, Stage = AttemptStage.Execute, LatencyMs = 609, Sql = history.FinalSql });

        await using (var db = database.CreateContext())
        {
            db.QueryHistory.Add(history);
            await db.SaveChangesAsync();
        }

        await using (var db = database.CreateContext())
        {
            var loaded = await db.QueryHistory.Include(h => h.Attempts).Include(h => h.User).SingleAsync(h => h.Id == history.Id);
            Assert.Equal(Roles.User, loaded.User!.Role);
            Assert.Equal(16097, loaded.TotalMs);
            Assert.Equal(AttemptStage.Execute, Assert.Single(loaded.Attempts).Stage);
            Assert.True(loaded.CreatedAt > DateTimeOffset.UtcNow.AddMinutes(-1));
        }
    }

    // --- helpers (plain SQL, so the constraints are tested in the database, not in EF) ---

    private async Task<NpgsqlConnection> Open()
    {
        var conn = new NpgsqlConnection(database.ConnectionString);
        await conn.OpenAsync();
        return conn;
    }

    private async Task<List<string>> Strings(string sql, params (string Name, object Value)[] parameters)
    {
        await using var conn = await Open();
        await using var cmd = Command(conn, sql, parameters);
        await using var reader = await cmd.ExecuteReaderAsync();
        var values = new List<string>();
        while (await reader.ReadAsync())
        {
            values.Add(reader.GetString(0));
        }

        return values;
    }

    private static NpgsqlCommand Command(NpgsqlConnection conn, string sql, (string Name, object Value)[] parameters)
    {
        var cmd = new NpgsqlCommand(sql, conn);
        foreach (var (name, value) in parameters)
        {
            cmd.Parameters.AddWithValue(name, value);
        }

        return cmd;
    }

    private static async Task Execute(NpgsqlConnection conn, string sql, params (string Name, object Value)[] parameters)
    {
        await using var cmd = Command(conn, sql, parameters);
        await cmd.ExecuteNonQueryAsync();
    }

    private static async Task<T> Scalar<T>(NpgsqlConnection conn, string sql, params (string Name, object Value)[] parameters)
    {
        await using var cmd = Command(conn, sql, parameters);
        return (T)(await cmd.ExecuteScalarAsync())!;
    }

    private static async Task<Guid> InsertUser(NpgsqlConnection conn, string email, string? role = Roles.User)
    {
        var id = Guid.NewGuid();
        var sql = role is null
            ? "INSERT INTO users (id, email, password_hash) VALUES (@id, @email, 'hash')"
            : "INSERT INTO users (id, email, password_hash, role) VALUES (@id, @email, 'hash', @role)";
        await using var cmd = Command(conn, sql, [("id", id), ("email", email)]);
        if (role is not null)
        {
            cmd.Parameters.AddWithValue("role", role);
        }

        await cmd.ExecuteNonQueryAsync();
        return id;
    }

    private static async Task<Guid> InsertHistory(
        NpgsqlConnection conn, Guid userId, string status = HistoryStatus.Success, Guid? rerunOf = null)
    {
        var id = Guid.NewGuid();
        await using var cmd = Command(conn,
            "INSERT INTO query_history (id, user_id, question, status, correlation_id, rerun_of_id) " +
            "VALUES (@id, @user, 'q?', @status, 'cid', @rerun)",
            [("id", id), ("user", userId), ("status", status), ("rerun", (object?)rerunOf ?? DBNull.Value)]);
        await cmd.ExecuteNonQueryAsync();
        return id;
    }

    private static Task InsertAttempt(NpgsqlConnection conn, Guid historyId, short attemptNo, string stage = AttemptStage.Execute) =>
        Execute(conn,
            "INSERT INTO query_attempts (id, history_id, attempt_no, stage, latency_ms) VALUES (@id, @h, @n, @s, 10)",
            ("id", Guid.NewGuid()), ("h", historyId), ("n", attemptNo), ("s", stage));
}
