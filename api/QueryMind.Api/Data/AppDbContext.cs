using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace QueryMind.Api.Data;

/// <summary>The app database: users, question history and attempts (design §5.1).</summary>
public sealed class AppDbContext(DbContextOptions<AppDbContext> options) : DbContext(options)
{
    public DbSet<User> Users => Set<User>();
    public DbSet<QueryHistory> QueryHistory => Set<QueryHistory>();
    public DbSet<QueryAttempt> QueryAttempts => Set<QueryAttempt>();

    /// <summary>Npgsql + snake_case names, shared by the app, the design-time factory and tests.</summary>
    public static DbContextOptionsBuilder<AppDbContext> Configure(
        DbContextOptionsBuilder<AppDbContext> builder, string connectionString) =>
        builder.UseNpgsql(connectionString).UseSnakeCaseNamingConvention();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        ArgumentNullException.ThrowIfNull(modelBuilder);
        modelBuilder.HasPostgresExtension("citext");

        modelBuilder.Entity<User>(e =>
        {
            e.ToTable("users", t => t.HasCheckConstraint("ck_users_role", InList("role", Roles.All)));
            e.HasKey(u => u.Id);
            e.Property(u => u.Email).HasColumnType("citext");
            e.HasIndex(u => u.Email).IsUnique();
            e.Property(u => u.Role).HasDefaultValue(Roles.User);
            e.Property(u => u.CreatedAt).HasDefaultValueSql("now()");
        });

        modelBuilder.Entity<QueryHistory>(e =>
        {
            e.ToTable("query_history", t => t.HasCheckConstraint(
                "ck_query_history_status", InList("status", HistoryStatus.All)));
            e.HasKey(h => h.Id);
            e.Property(h => h.CreatedAt).HasDefaultValueSql("now()");
            // A user with history cannot be deleted by accident; deleting an original question
            // keeps its re-runs and clears their link.
            e.HasOne(h => h.User).WithMany(u => u.History).HasForeignKey(h => h.UserId)
                .OnDelete(DeleteBehavior.Restrict);
            e.HasOne(h => h.RerunOf).WithMany().HasForeignKey(h => h.RerunOfId)
                .OnDelete(DeleteBehavior.SetNull);
            // Own history list (newest first) and admin list / latency reports (design §5.1).
            e.HasIndex(h => new { h.UserId, h.CreatedAt }).IsDescending(false, true)
                .HasDatabaseName("ix_query_history_user_id_created_at");
            e.HasIndex(h => h.CreatedAt).HasDatabaseName("ix_query_history_created_at");
        });

        modelBuilder.Entity<QueryAttempt>(e =>
        {
            e.ToTable("query_attempts", t =>
            {
                t.HasCheckConstraint("ck_query_attempts_stage", InList("stage", AttemptStage.All));
                t.HasCheckConstraint("ck_query_attempts_attempt_no", "attempt_no >= 1");
            });
            e.HasKey(a => a.Id);
            e.HasOne(a => a.History).WithMany(h => h.Attempts).HasForeignKey(a => a.HistoryId)
                .OnDelete(DeleteBehavior.Cascade);
            e.HasIndex(a => new { a.HistoryId, a.AttemptNo }).IsUnique();
        });
    }

    private static string InList(string column, IEnumerable<string> values) =>
        $"{column} IN ({string.Join(", ", values.Select(v => $"'{v}'"))})";
}

/// <summary>Lets <c>dotnet ef migrations add</c> build the model without starting the app.</summary>
internal sealed class DesignTimeAppDbContextFactory : IDesignTimeDbContextFactory<AppDbContext>
{
    public AppDbContext CreateDbContext(string[] args) =>
        new(AppDbContext.Configure(new DbContextOptionsBuilder<AppDbContext>(),
            "Host=localhost;Database=querymind_design_time").Options);
}
